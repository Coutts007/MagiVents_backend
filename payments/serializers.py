from django.utils import timezone
from rest_framework import serializers

from tickets.serializers import BookingSerializer


def payment_summary(payment):
    from .providers import provider_for_payment
    return {
        'id': str(payment.id),
        'provider': payment.provider,
        'method': payment.method,
        'status': payment.status,
        'reference': payment.reference,
        'amount': float(payment.amount),
        'failureReason': payment.failure_reason or None,
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
