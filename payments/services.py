"""
The paid booking lifecycle.

    pending ──(provider reports success, amount matches)──▶ confirmed   (ticket code issued, email sent)
       │
       ├──(provider reports failure)──▶ failed    (held tickets released)
       └──(hold time runs out)────────▶ expired   (held tickets released)

Every provider callback, status query and the simulated provider go through `record_success` /
`record_failure`, which are idempotent: a repeated or late callback never confirms twice, never
releases tickets twice, and only revives a released booking after re-checking availability.

Rows are locked in one order everywhere (gathering, booking, payment, tier) so concurrent bookings,
callbacks and expiry cannot deadlock on PostgreSQL.
"""
import logging
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from gatherings.models import Gathering, TicketTier
from tickets.emails import send_booking_confirmation
from tickets.models import Booking, PromoCode
from .models import Payment

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """A payment could not be started; the message is safe to show to the attendee."""


def hold_duration():
    return timedelta(minutes=settings.PAYMENT_HOLD_MINUTES)


def generate_ticket_code():
    while True:
        code = f'MV-{random.randint(100000, 999999)}'
        if not Booking.objects.filter(ticket_code=code).exists():
            return code


# --- Ticket holds ------------------------------------------------------------------------------

def _locked(booking_id):
    """Locks the gathering, booking and tier rows of a booking, in the global lock order."""
    gathering_id = Booking.objects.values_list('gathering_id', flat=True).get(id=booking_id)
    gathering = Gathering.objects.select_for_update().get(id=gathering_id)
    booking = Booking.objects.select_for_update().get(id=booking_id)
    tier = TicketTier.objects.select_for_update().filter(id=booking.tier_id).first() if booking.tier_id else None
    return gathering, booking, tier


def _release(gathering, booking, tier, new_status):
    """Gives the booking's tickets back and closes it. The caller holds the row locks."""
    if booking.holds_tickets:
        if tier is not None:
            tier.available = min(tier.quantity, tier.available + booking.quantity)
            if booking.is_sale_price:
                # The tickets go back to the flash-sale allowance they came from
                tier.sale_sold = max(0, tier.sale_sold - booking.quantity)
            tier.save(update_fields=['available', 'sale_sold'])
        gathering.attendee_count = max(0, gathering.attendee_count - booking.people)
        if gathering.status == 'sold_out' and gathering.attendee_count < gathering.capacity:
            gathering.status = 'published'
        gathering.save(update_fields=['attendee_count', 'status'])
        booking.holds_tickets = False
        if booking.promo_code_id:
            # The discount was never used, so the code gets its use back
            PromoCode.objects.filter(id=booking.promo_code_id, use_count__gt=0).update(use_count=F('use_count') - 1)
    booking.status = new_status
    booking.expires_at = None
    booking.save(update_fields=['holds_tickets', 'status', 'expires_at'])


def _reacquire(gathering, booking, tier):
    """Takes the booking's tickets again after a release, if they are still available."""
    if booking.holds_tickets:
        return True
    if gathering.status == 'draft' or gathering.capacity - gathering.attendee_count < booking.people:
        return False
    if booking.tier_id and (tier is None or tier.available < booking.quantity):
        return False
    if (booking.is_sale_price and tier is not None and tier.sale_quantity is not None
            and tier.sale_sold + booking.quantity > tier.sale_quantity):
        # The flash-sale allowance has gone to other buyers in the meantime
        return False
    if tier is not None:
        tier.available -= booking.quantity
        if booking.is_sale_price:
            tier.sale_sold += booking.quantity
        tier.save(update_fields=['available', 'sale_sold'])
    gathering.attendee_count += booking.people
    if gathering.attendee_count >= gathering.capacity:
        gathering.status = 'sold_out'
    gathering.save(update_fields=['attendee_count', 'status'])
    booking.holds_tickets = True
    booking.save(update_fields=['holds_tickets'])
    return True


def release_expired_holds(gathering_id=None):
    """Expires pending bookings whose hold ran out. Called lazily wherever availability is read."""
    stale = Booking.objects.filter(status='pending', expires_at__lt=timezone.now())
    if gathering_id is not None:
        stale = stale.filter(gathering_id=gathering_id)
    released = 0
    for booking_id in list(stale.values_list('id', flat=True)):
        with transaction.atomic():
            gathering, booking, tier = _locked(booking_id)
            if booking.status == 'pending' and booking.expires_at and booking.expires_at < timezone.now():
                _release(gathering, booking, tier, 'expired')
                released += 1
    return released


def release_expired_holds_soon(gathering_id=None):
    """release_expired_holds() for pages that only show availability: at most once per HOLDS_SWEEP_SECONDS
    (per event, or for all events), instead of a query on every page view. Bookings always sweep first."""
    from django.core.cache import cache
    if cache.add(f'holds-sweep:{gathering_id or "*"}', 1, settings.HOLDS_SWEEP_SECONDS):
        return release_expired_holds(gathering_id)
    return 0


# --- Confirmation --------------------------------------------------------------------------------

def confirm_free_booking(booking):
    """Free bookings need no payment. The caller holds the row locks (inside the booking transaction)."""
    booking.status = 'confirmed'
    booking.confirmed_at = timezone.now()
    booking.ticket_code = generate_ticket_code()
    booking.save(update_fields=['status', 'confirmed_at', 'ticket_code'])
    queue_confirmation_email(booking)


def _confirm(booking, payment):
    booking.status = 'confirmed'
    booking.confirmed_at = timezone.now()
    booking.expires_at = None
    booking.ticket_code = booking.ticket_code or generate_ticket_code()
    if payment.provider in ('mpesa_stk', 'mpesa_paybill') or (payment.provider == 'simulated' and payment.method == 'mpesa'):
        booking.mpesa_receipt_number = payment.receipt_number
        booking.mpesa_phone_number = payment.phone_number or booking.mpesa_phone_number
    booking.save(update_fields=['status', 'confirmed_at', 'expires_at', 'ticket_code',
                                'mpesa_receipt_number', 'mpesa_phone_number'])
    queue_confirmation_email(booking)


def queue_confirmation_email(booking):
    """Sends the ticket email after the booking commits. With BOOKING_EMAILS_IN_BACKGROUND the booking is only
    marked, and `manage.py send_booking_emails` builds the PDF and talks to the mail server outside any request."""
    if settings.BOOKING_EMAILS_IN_BACKGROUND:
        Booking.objects.filter(id=booking.id).update(confirmation_email_due=True)
    else:
        transaction.on_commit(lambda: send_booking_confirmation(booking))


def _is_listing_fee(payment_id):
    return Payment.objects.filter(id=payment_id, purpose='listing_fee').exists()


@transaction.atomic
def record_success(payment_id, amount, *, receipt='', provider_reference='', payload=None, source='callback'):
    """The provider says this payment went through. Safe to call any number of times."""
    if _is_listing_fee(payment_id):
        from listings.services import record_fee_success
        return record_fee_success(payment_id, amount, receipt=receipt, provider_reference=provider_reference,
                                  payload=payload, source=source)
    booking_id = Payment.objects.values_list('booking_id', flat=True).get(id=payment_id)
    gathering, booking, tier = _locked(booking_id)
    payment = Payment.objects.select_for_update().get(id=payment_id)
    if payload is not None:
        payment.log_payload(source, payload)

    if payment.status == 'succeeded':
        if provider_reference and payment.provider_reference and provider_reference != payment.provider_reference:
            # A second, different transaction for the same account number: money to give back
            payment.needs_refund = True
            payment.failure_reason = f'A second payment ({provider_reference}) was received for this booking.'
            logger.warning('Second payment %s for payment %s needs a refund', provider_reference, payment.id)
        # Otherwise a repeated callback: nothing more to do
        payment.save(update_fields=['raw_payloads', 'needs_refund', 'failure_reason', 'updated_at'])
        return booking

    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    payment.status = 'succeeded'
    payment.completed_at = timezone.now()
    payment.receipt_number = receipt or payment.receipt_number
    payment.provider_reference = provider_reference or payment.provider_reference

    if amount != booking.total_price:
        # Money arrived, but not the amount due: the booking is not confirmed and the owner must refund
        payment.status = 'failed'
        payment.needs_refund = True
        payment.failure_reason = f'Paid KSh {amount:,.0f} but KSh {booking.total_price:,.0f} was due.'
        payment.save()
        logger.warning('Amount mismatch on payment %s', payment.id)
        return booking

    if booking.status == 'confirmed':
        # Paid twice (for example a retry after a slow callback): keep the first, flag this one
        payment.needs_refund = True
        payment.failure_reason = 'The booking was already paid.'
    elif booking.status == 'pending' or _reacquire(gathering, booking, tier):
        _confirm(booking, payment)
    else:
        # The hold had been released and the tickets are gone: never oversell
        payment.needs_refund = True
        payment.failure_reason = 'Paid after the booking had expired and the tickets were no longer available.'
        logger.warning('Late payment %s for released booking %s needs a refund', payment.id, booking.id)
    payment.save()
    return booking


@transaction.atomic
def record_failure(payment_id, reason, *, payload=None, source='callback'):
    """The provider says this payment did not go through. Releases the booking's tickets once."""
    if _is_listing_fee(payment_id):
        from listings.services import record_fee_failure
        return record_fee_failure(payment_id, reason, payload=payload, source=source)
    booking_id = Payment.objects.values_list('booking_id', flat=True).get(id=payment_id)
    gathering, booking, tier = _locked(booking_id)
    payment = Payment.objects.select_for_update().get(id=payment_id)
    if payload is not None:
        payment.log_payload(source, payload)
    if payment.status != 'pending':
        # Late or repeated: a payment that already succeeded is never undone by a failure report
        payment.save(update_fields=['raw_payloads', 'updated_at'])
        return booking

    payment.status = 'failed'
    payment.failure_reason = (reason or 'The payment was not completed.')[:255]
    payment.completed_at = timezone.now()
    payment.save()

    # Another attempt may still be running (e.g. switched from M-Pesa to card)
    if booking.status == 'pending' and not booking.payments.filter(status='pending').exclude(id=payment.id).exists():
        _release(gathering, booking, tier, 'failed')
    return booking


# --- Starting and checking payments ---------------------------------------------------------------

def _new_reference():
    """A short, unambiguous reference; also the Paybill account number the attendee types."""
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    while True:
        reference = 'MV' + ''.join(random.choice(alphabet) for _ in range(8))
        if not Payment.objects.filter(reference=reference).exists():
            return reference


def start_payment(booking, method, mpesa_mode='stk', phone=''):
    """Opens a payment attempt with the configured provider. Retrying a failed booking re-holds its tickets."""
    from .providers import get_provider

    provider = get_provider(method, mpesa_mode)
    if not provider.is_configured():
        raise PaymentError(provider.not_configured_message)

    with transaction.atomic():
        gathering, booking, tier = _locked(booking.id)
        if booking.status == 'confirmed':
            raise PaymentError('This booking is already paid.')
        if booking.status in ('failed', 'expired'):
            if not _reacquire(gathering, booking, tier):
                raise PaymentError('Sorry, these tickets are no longer available.')
        # Retrying cancels earlier attempts that never finished, so only one can confirm the booking
        booking.payments.filter(status='pending').update(
            status='failed', failure_reason='Replaced by a new payment attempt.', completed_at=timezone.now())
        booking.status = 'pending'
        booking.expires_at = timezone.now() + hold_duration()
        booking.payment_method = 'card' if method == 'card' else 'mpesa'
        booking.mpesa_mode = mpesa_mode if method == 'mpesa' else ''
        booking.mpesa_phone_number = phone if method == 'mpesa' else ''
        booking.save(update_fields=['status', 'expires_at', 'payment_method', 'mpesa_mode', 'mpesa_phone_number'])
        payment = Payment.objects.create(
            booking=booking, provider=provider.name, method=method, reference=_new_reference(),
            amount=booking.total_price, currency=booking.currency, phone_number=phone,
        )

    # The provider call happens outside the transaction so no row stays locked during HTTP
    try:
        provider.initiate(payment)
    except PaymentError as exc:
        record_failure(payment.id, str(exc), source='initiate')
        raise
    payment.save()
    return payment


def refresh_payment(payment):
    """Asks the provider about a pending payment (fallback when a callback is slow or never comes)."""
    from .providers import provider_for_payment

    if payment.status != 'pending':
        return payment
    provider = provider_for_payment(payment)
    now = timezone.now()
    if (now - payment.created_at).total_seconds() < provider.min_query_age:
        return payment
    if payment.last_queried_at and (now - payment.last_queried_at).total_seconds() < provider.query_interval:
        return payment
    Payment.objects.filter(id=payment.id).update(last_queried_at=now)
    try:
        result = provider.query(payment)
    except PaymentError:
        logger.exception('Status query failed for payment %s', payment.id)
        return payment
    if result is None or result.status == 'pending':
        return payment
    if result.status == 'succeeded':
        record_success(payment.id, result.amount, receipt=result.receipt,
                       provider_reference=result.provider_reference, payload=result.raw, source='query')
    else:
        record_failure(payment.id, result.reason, payload=result.raw, source='query')
    payment.refresh_from_db()
    return payment
