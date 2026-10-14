from django.utils import timezone
from rest_framework import serializers

from tickets.serializers import BookingSerializer


GENERIC_FAILURE = 'The payment was not completed.'


def public_failure_reasons():
    """The failure messages written for attendees. Anything else in failure_reason (provider text, internal
    notes such as amount mismatches) is for staff only."""
    from .providers.daraja import STK_RESULT_MESSAGES, UNAVAILABLE as MPESA_UNAVAILABLE
    from .providers.paystack import UNAVAILABLE as CARD_UNAVAILABLE
    from .providers.simulated import SIMULATED_FAILURES
    return {
        *STK_RESULT_MESSAGES.values(), *SIMULATED_FAILURES.values(), MPESA_UNAVAILABLE, CARD_UNAVAILABLE,
        'An M-Pesa request is already open on that phone. Wait a minute and try again.',
        'M-Pesa could not send the payment request. Check the number and try again.',
        'The card payment could not be started. Please try again or pay with M-Pesa.',
        'The card payment was made in the wrong currency.', 'The card payment failed.',
    }


def public_failure_reason(payment):
    if not payment.failure_reason:
        return None
    return payment.failure_reason if payment.failure_reason in public_failure_reasons() else GENERIC_FAILURE


def payment_summary(payment):
    from .providers import provider_for_payment
    return {
        'id': str(payment.id),
        'provider': payment.provider,
        'method': payment.method,
        'status': payment.status,
        'reference': payment.reference,
        'amount': float(payment.amount),
        'failureReason': public_failure_reason(payment),
        'authorizationUrl': payment.authorization_url or None,
        'receiptNumber': payment.receipt_number or None,
        'instructions': provider_for_payment(payment).instructions(payment),
    }


def booking_status_payload(booking, request):
    """The booking (ticket fields stay empty until confirmed) plus its latest payment attempt."""
    data = BookingSerializer(booking, context={'request': request}).data
    payment = booking.payments.order_by('-created_at').first()
    data['payment'] = payment_summary(payment) if payment else None
    data['holdSecondsLeft'] = (
        max(0, int((booking.expires_at - timezone.now()).total_seconds()))
        if booking.status == 'pending' and booking.expires_at else None
    )
    return data


class PayRequestSerializer(serializers.Serializer):
    paymentMethod = serializers.ChoiceField(choices=['mpesa', 'card'])
    mpesaMode = serializers.ChoiceField(choices=['stk', 'paybill'], required=False, default='stk')
    mpesaPhoneNumber = serializers.CharField(required=False, allow_blank=True, max_length=30)
