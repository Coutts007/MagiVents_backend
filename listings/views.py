from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from gatherings.models import Gathering
from payments.phone import normalize_kenyan_phone
from payments.services import refresh_payment
from payouts.views import own_gathering_or_403
from .services import fee_payload, get_fee, live_pending_payment, refresh_status, start_fee_payment


def listing_fee_state(gathering):
    fee = get_fee(gathering)
    pending = live_pending_payment(fee)
    if pending is not None:
        # Fallback for confirmations that are slow or never arrive (the simulated gateway answers here)
        refresh_payment(pending)
        fee.refresh_from_db()
        gathering.refresh_from_db()
    refresh_status(fee)
    return {**fee_payload(fee, include_platform=True), 'eventId': str(gathering.id),
            'eventStatus': gathering.status, 'category': gathering.category}


@extend_schema(responses=OpenApiTypes.OBJECT)
class ListingFeeView(APIView):
    """The event's listing fee: amount due, status, the platform's payment details and the latest attempt.
    Polled by the organizer's "Pay to publish" step. Owner only."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, gathering_id):
        return Response(listing_fee_state(own_gathering_or_403(request, gathering_id)))


class PayListingFeeSerializer(serializers.Serializer):
    mpesaPhoneNumber = serializers.CharField(max_length=30)
    # Set to pay the difference for moving a paid event to a dearer category
    category = serializers.ChoiceField(choices=Gathering.CATEGORY_CHOICES, required=False, allow_blank=True)


@extend_schema(request=PayListingFeeSerializer, responses=OpenApiTypes.OBJECT)
class PayListingFeeView(APIView):
    """Starts an online listing-fee payment. When it is confirmed the event is published (if it was asked)."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'payment_attempts'

    def get_throttles(self):
        from rest_framework.throttling import ScopedRateThrottle
        return [ScopedRateThrottle()]

    def post(self, request, gathering_id):
        gathering = own_gathering_or_403(request, gathering_id)
        data = PayListingFeeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            phone = normalize_kenyan_phone(data.validated_data['mpesaPhoneNumber'])
        except ValueError as exc:
            raise serializers.ValidationError({'mpesaPhoneNumber': str(exc)})
        start_fee_payment(gathering.id, request.user, phone, data.validated_data.get('category') or '')
        return Response(listing_fee_state(Gathering.objects.get(id=gathering.id)))
