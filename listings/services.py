"""
Listing fees: an organizer pays the platform a fee, set by the admin per category, before an event can be
published. An event can be published only when its fee is paid or waived, or its category's fee is 0.

    created (draft) ──▶ unpaid ──(organizer pays)──▶ pending ──(provider confirms)──▶ paid ──▶ published
                         │   ▲                          │                               (if publish was asked)
                         │   └──(payment fails)─────────┘
                         ├──(admin waives, with a reason)──────▶ waived
                         └──(admin records a payment by hand)──▶ paid

The payment state lives on the server and goes through the same Payment model and provider interface as
ticket payments (payments.services.record_success / record_failure dispatch here). Confirming is idempotent:
a repeated confirmation never publishes twice, notifies twice, or records the money twice.

Rows are locked in the order gathering -> listing fee -> payment, matching the ticket lifecycle's
gathering -> booking -> payment -> tier.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status as http

from gatherings.models import Gathering
from gatherings.publishing import PublishBlocked
from payments.models import Payment
from payments.services import PaymentError, _new_reference, refresh_payment
from .models import CategoryFee, ListingFee, PlatformPaymentAccount

logger = logging.getLogger(__name__)


class ListingFeeDue(PublishBlocked):
    status_code = http.HTTP_402_PAYMENT_REQUIRED
    default_code = 'listing_fee_due'


class ListingFeeError(PublishBlocked):
    """A listing-fee request that cannot be done (nothing to pay, not available, ...)."""
    default_code = 'listing_fee_error'


def kes(amount):
    return f'KSh {int(amount):,}'


def fee_for(category):
    return CategoryFee.fee_for(category)


def get_fee(gathering):
    return ListingFee.objects.get_or_create(gathering=gathering)[0]


def amount_due(fee, category=None, fee_table=None):
    """What the organizer still has to pay for the event to be in `category` (default: its current one)."""
    category = category or fee.gathering.category
    level = fee_table.get(category, 0) if fee_table is not None else fee_for(category)
    if not fee.cleared:
        return level
    if category == fee.cleared_category:
        return 0
    # Moving a paid event to a dearer category costs the difference; a cheaper one is free (no refund)
    return max(0, level - fee.cleared_amount)


def live_pending_payment(fee):
    """A payment attempt still running. Abandoned attempts stop counting after the payment hold time."""
    since = timezone.now() - timedelta(minutes=settings.PAYMENT_HOLD_MINUTES)
    return fee.payments.filter(status='pending', created_at__gte=since).order_by('-created_at').first()


def compute_status(fee):
    if live_pending_payment(fee) is not None:
        return 'pending'
    if amount_due(fee) > 0:
        return 'unpaid'
    if fee.grandfathered or fee.amount_paid > 0:
        return 'paid'
    if fee.amount_waived > 0:
        return 'waived'
    return 'exempt'


def refresh_status(fee):
    new_status = compute_status(fee)
    if fee.status != new_status:
        fee.status = new_status
        fee.save(update_fields=['status', 'updated_at'])
    return fee


def refresh_category(category):
    """After an admin changes a category's fee: events not yet cleared now owe the new amount."""
    for fee in ListingFee.objects.filter(cleared=False, gathering__category=category).select_related('gathering'):
        refresh_status(fee)


# --- Rules used by the publish gate and Gathering.save() ------------------------------------------------

def ensure_fee_settled(gathering):
    """Raises ListingFeeDue (HTTP 402, with the amount) when the event's fee is not paid or waived."""
    if gathering._state.adding:
        due = fee_for(gathering.category)
        fee = None
    else:
        fee = get_fee(gathering)
        due = amount_due(fee)
    if due <= 0:
        return
    if fee is not None and live_pending_payment(fee) is not None:
        raise ListingFeeDue(
            f'The listing-fee payment of {kes(due)} is still being confirmed. The event will be published as '
            f'soon as it is.', code='listing_fee_pending', amountDue=due, eventId=str(gathering.id),
        )
    raise ListingFeeDue(
        f'Pay the listing fee of {kes(due)} to publish this event.',
        amountDue=due, eventId=str(gathering.id), category=gathering.category,
    )


def ensure_category_change_allowed(gathering, previous_category):
    """A paid event moving to a dearer category pays the difference before the change takes effect."""
    fee = ListingFee.objects.filter(gathering=gathering).first()
    if fee is None or not fee.cleared:
        return
    due = amount_due(fee, gathering.category)
    if due > 0:
        raise ListingFeeDue(
            f'Moving this event to {gathering.category} costs {kes(due)} more in listing fees. Pay the difference '
            f'first; the category changes as soon as the payment is confirmed.',
            code='category_upgrade_due', amountDue=due, eventId=str(gathering.id),
            category=previous_category, targetCategory=gathering.category,
        )


def after_gathering_saved(gathering, previous_status, previous_category):
    fee = get_fee(gathering)
    changed = False
    if gathering.is_public and not fee.cleared:
        # Published while nothing was due (category fee 0): the fee is settled for good, once
        fee.cleared, fee.cleared_category, fee.cleared_amount = True, gathering.category, fee_for(gathering.category)
        changed = True
    elif fee.cleared and previous_category and gathering.category != previous_category \
            and amount_due(fee, gathering.category) == 0:
        fee.cleared_category = gathering.category
        changed = True
    if gathering.is_public and fee.publish_requested:
        fee.publish_requested = False
        changed = True
    if changed:
        fee.save()
    refresh_status(fee)


# --- Paying ---------------------------------------------------------------------------------------------

def listing_fee_provider():
    """The gateway that takes listing fees online, or None when they are paid by hand and confirmed by an admin.

    Only the simulated gateway is wired up. M-Pesa (Daraja STK Push) or a card gateway can be plugged in here
    later, through the same PaymentProvider interface that ticket payments use (payments/providers/base.py).
    """
    from payments.providers import SimulatedProvider, live_mode
    if live_mode():
        return None
    return SimulatedProvider('mpesa', 'stk')


def online_payments_available():
    return listing_fee_provider() is not None


def _cancel_pending(fee, reason):
    fee.payments.filter(status='pending').update(status='failed', failure_reason=reason, completed_at=timezone.now())


def start_fee_payment(gathering_id, user, phone, target_category=''):
    """Starts an online listing-fee payment (the fee itself, or the difference for a dearer category)."""
    from gatherings.publishing import ensure_publishable

    provider = listing_fee_provider()
    if provider is None:
        raise ListingFeeError(
            'Listing fees cannot be paid online yet. Pay with the details shown, quoting the reference; an '
            'administrator will confirm the payment and the event will then be published.',
            code='listing_fee_offline', status_code=http.HTTP_503_SERVICE_UNAVAILABLE,
        )
    with transaction.atomic():
        gathering = Gathering.objects.select_for_update().select_related('organizer').get(id=gathering_id)
        fee = ListingFee.objects.select_for_update().get(pk=get_fee(gathering).pk)
        if target_category:
            if target_category not in Gathering.CATEGORIES or target_category == gathering.category:
                raise ListingFeeError('Choose the new category for this event.')
            if not fee.cleared:
                raise ListingFeeError('This event has not been listed yet: change its category in the event form.')
            due = amount_due(fee, target_category)
        else:
            due = amount_due(fee)
            # Never take money for an event that could not go live afterwards
            ensure_publishable(gathering, check_fee=False)
        if due <= 0:
            raise ListingFeeError('There is no listing fee to pay for this event.', code='listing_fee_not_due')
        _cancel_pending(fee, 'Replaced by a new payment attempt.')
        payment = Payment.objects.create(
            purpose='listing_fee', listing_fee=fee, provider=provider.name, method='mpesa',
            reference=_new_reference(), amount=Decimal(due), phone_number=phone, target_category=target_category,
        )
        if not target_category:
            fee.publish_requested = True
        fee.status = 'pending'
        fee.save(update_fields=['publish_requested', 'status', 'updated_at'])

    # The provider call happens outside the transaction so no row stays locked during HTTP
    try:
        provider.initiate(payment)
    except PaymentError as exc:
        record_fee_failure(payment.id, str(exc), source='initiate')
        raise ListingFeeError(str(exc))
    payment.save()
    return refresh_payment(payment)


def _locked(payment_id):
    fee_id = Payment.objects.values_list('listing_fee_id', flat=True).get(id=payment_id)
    gathering_id = ListingFee.objects.values_list('gathering_id', flat=True).get(id=fee_id)
    gathering = Gathering.objects.select_for_update().select_related('organizer').get(id=gathering_id)
    fee = ListingFee.objects.select_for_update().get(id=fee_id)
    payment = Payment.objects.select_for_update().get(id=payment_id)
    return gathering, fee, payment


def _publish_if_requested(gathering, fee):
    """The organizer asked to publish: do it now that the fee is settled, if every other rule is met."""
    from gatherings.publishing import ensure_publishable
    if not fee.publish_requested or gathering.status != 'draft':
        fee.save()
        return False
    fee.publish_requested = False
    # The publish rules read the fee from the database, so it is saved first
    fee.save()
    try:
        ensure_publishable(gathering)
    except PublishBlocked as exc:
        logger.info('Listing fee settled for %s but it cannot be published yet: %s', gathering.id, exc.message)
        return False
    gathering.status = 'sold_out' if gathering.is_full else 'published'
    gathering.save(update_fields=['status'])
    return True


def _credit(gathering, fee, amount, target_category=''):
    """Records `amount` KES towards the event's fee (or towards moving it to `target_category`)."""
    category = target_category or gathering.category
    fee.cleared_amount = (fee.cleared_amount if fee.cleared else 0) + int(amount)
    fee.cleared = True
    fee.cleared_category = category
    # Saved before the category changes, because Gathering.save() checks the fee covers the new category
    fee.save()
    if target_category and gathering.category != target_category:
        # The category change paid for takes effect now
        gathering.category = target_category
        gathering.save(update_fields=['category'])


@transaction.atomic
def record_fee_success(payment_id, amount, *, receipt='', provider_reference='', payload=None, source='callback'):
    """The provider says this listing-fee payment went through. Safe to call any number of times."""
    from notifications.services import notify_admins

    gathering, fee, payment = _locked(payment_id)
    if payload is not None:
        payment.log_payload(source, payload)
    if payment.status == 'succeeded':
        # A repeated confirmation: the money is already recorded and the admins already told
        payment.save(update_fields=['raw_payloads', 'updated_at'])
        return fee

    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    payment.status = 'succeeded'
    payment.completed_at = timezone.now()
    payment.receipt_number = receipt or payment.receipt_number
    payment.provider_reference = provider_reference or payment.provider_reference
    target = payment.target_category

    if amount != payment.amount:
        payment.status = 'failed'
        payment.needs_refund = True
        payment.failure_reason = f'Paid {kes(amount)} but {kes(payment.amount)} was due.'
        payment.save()
        refresh_status(fee)
        return fee
    if amount_due(fee, target or None) <= 0:
        # Already settled another way (an admin recorded or waived it meanwhile): this money goes back
        payment.needs_refund = True
        payment.failure_reason = 'The listing fee had already been settled.'
        payment.save()
        refresh_status(fee)
        return fee

    payment.save()
    _credit(gathering, fee, amount, target)
    fee.amount_paid += int(amount)
    fee.paid_at = payment.completed_at
    fee.payment_reference = payment.receipt_number or payment.reference
    published = _publish_if_requested(gathering, fee)
    fee.save()
    refresh_status(fee)

    organizer = gathering.organizer
    what = f'moving "{gathering.title}" to {target}' if target else f'"{gathering.title}"'
    notify_admins(
        'listing_fee_paid', f'Listing fee paid: {gathering.title}',
        f'{organizer.first_name or organizer.email} paid {kes(amount)} for {what} '
        f'(reference {fee.payment_reference}).' + (' The event is now published.' if published else ''),
        dedupe_key=f'listing_fee_paid:{payment.id}', gathering=gathering,
    )
    return fee


@transaction.atomic
def record_fee_failure(payment_id, reason, *, payload=None, source='callback'):
    from notifications.services import notify_admins

    gathering, fee, payment = _locked(payment_id)
    if payload is not None:
        payment.log_payload(source, payload)
    if payment.status != 'pending':
        payment.save(update_fields=['raw_payloads', 'updated_at'])
        return fee
    payment.status = 'failed'
    payment.failure_reason = (reason or 'The payment was not completed.')[:255]
    payment.completed_at = timezone.now()
    payment.save()
    refresh_status(fee)
    notify_admins(
        'listing_fee_failed', f'Listing-fee payment failed: {gathering.title}',
        f'A listing-fee payment of {kes(payment.amount)} by {gathering.organizer.email} did not go through: '
        f'{payment.failure_reason}',
        dedupe_key=f'listing_fee_failed:{payment.id}', gathering=gathering,
    )
    return fee


# --- Admin actions ---------------------------------------------------------------------------------------

def _locked_fee(gathering_id):
    gathering = Gathering.objects.select_for_update().select_related('organizer').get(id=gathering_id)
    fee = ListingFee.objects.select_for_update().get(pk=get_fee(gathering).pk)
    return gathering, fee


@transaction.atomic
def waive_fee(gathering_id, admin, reason):
    gathering, fee = _locked_fee(gathering_id)
    due = amount_due(fee)
    if due <= 0:
        raise ListingFeeError('There is no listing fee due for this event.', code='listing_fee_not_due')
    _cancel_pending(fee, 'Cancelled: the listing fee was waived by an administrator.')
    _credit(gathering, fee, due)
    fee.amount_waived += due
    fee.waived_by, fee.waived_at, fee.waive_reason = admin, timezone.now(), reason
    _publish_if_requested(gathering, fee)
    fee.save()
    return refresh_status(fee)


@transaction.atomic
def mark_fee_paid(gathering_id, admin, reason, reference):
    """Records a listing fee the organizer paid outside MagiVents (e.g. to the platform Paybill)."""
    from notifications.services import notify_admins

    gathering, fee = _locked_fee(gathering_id)
    due = amount_due(fee)
    if due <= 0:
        raise ListingFeeError('There is no listing fee due for this event.', code='listing_fee_not_due')
    _cancel_pending(fee, 'Cancelled: the payment was recorded by an administrator.')
    now = timezone.now()
    payment = Payment.objects.create(
        purpose='listing_fee', listing_fee=fee, provider='manual', method='manual', reference=_new_reference(),
        provider_reference=reference, receipt_number=reference[:60], amount=Decimal(due), status='succeeded',
        completed_at=now,
    )
    payment.log_payload('admin', {'admin': admin.email, 'reason': reason, 'reference': reference})
    payment.save(update_fields=['raw_payloads'])
    _credit(gathering, fee, due)
    fee.amount_paid += due
    fee.paid_at = now
    fee.payment_reference = reference
    fee.marked_paid_by, fee.marked_paid_at, fee.mark_paid_reason = admin, now, reason
    published = _publish_if_requested(gathering, fee)
    fee.save()
    refresh_status(fee)
    notify_admins(
        'listing_fee_paid', f'Listing fee paid: {gathering.title}',
        f'{admin.email} recorded a listing-fee payment of {kes(due)} (reference {reference}).'
        + (' The event is now published.' if published else ''),
        dedupe_key=f'listing_fee_paid:{payment.id}', gathering=gathering, exclude=admin,
    )
    return fee


def grandfather(gathering):
    """Events that existed before listing fees, and the demo events, count as already paid."""
    fee = get_fee(gathering)
    fee.cleared, fee.grandfathered = True, True
    fee.cleared_category = gathering.category
    fee.cleared_amount = max(fee.cleared_amount, fee_for(gathering.category))
    fee.save()
    return refresh_status(fee)


# --- What the organizer sees ------------------------------------------------------------------------------

def platform_payment_payload(fee=None):
    account = PlatformPaymentAccount.load()
    return {
        'method': account.method,
        'methodLabel': account.get_method_display(),
        'businessNumber': account.business_number,
        # A Paybill without a fixed account number uses the event's own listing reference
        'accountNumber': (account.account_number or (fee.reference if fee else '')) if account.method == 'paybill' else '',
        'accountName': account.account_name,
        'instructions': account.instructions,
        'isConfigured': bool(account.business_number),
    }


def fee_payload(fee, *, fee_table=None, include_platform=False):
    gathering = fee.gathering
    level = fee_table.get(gathering.category, 0) if fee_table is not None else fee_for(gathering.category)
    data = {
        'status': fee.status,
        'amountDue': amount_due(fee, fee_table=fee_table),
        'categoryFee': level,
        'amountPaid': fee.amount_paid,
        'reference': fee.reference,
        'publishRequested': fee.publish_requested,
        'paidAt': fee.paid_at.isoformat() if fee.paid_at else None,
        'onlinePayment': online_payments_available(),
    }
    if include_platform:
        from payments.serializers import payment_summary
        latest = fee.payments.order_by('-created_at').first()
        data['payment'] = payment_summary(latest) if latest else None
        data['platform'] = platform_payment_payload(fee)
        data['clearedCategory'] = fee.cleared_category or None
    return data


def fee_table():
    return dict(CategoryFee.objects.values_list('category', 'amount'))
