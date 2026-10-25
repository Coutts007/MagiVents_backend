from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from tickets.models import Booking
from .phone import normalize_kenyan_phone
from .serializers import PayRequestSerializer, booking_status_payload
from .services import PaymentError, refresh_payment, release_expired_holds, start_payment


def get_booking(booking_id):
    return Booking.objects.select_related('gathering').filter(id=str(booking_id)).first()


@extend_schema(responses=OpenApiTypes.OBJECT)
class BookingStatusView(APIView):
    """Polled by checkout (guests included) until the booking is confirmed, failed or expired.
    The booking UUID is the key, so no account is needed."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, booking_id):
        booking = get_booking(booking_id)
        if booking is None:
            return Response({'error': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)
        if booking.status in ('pending', 'expired'):
            payment = booking.payments.filter(status='pending').order_by('-created_at').first()
            if payment:
                # Fallback for callbacks that are slow or never arrive. An expired booking whose payment never got
                # an answer keeps asking: a late success confirms it if the tickets are still available
                refresh_payment(payment)
            if booking.status == 'pending':
                release_expired_holds(booking.gathering_id)
            booking.refresh_from_db()
        return Response(booking_status_payload(booking, request))


@extend_schema(request=PayRequestSerializer, responses=OpenApiTypes.OBJECT)
class BookingPayView(APIView):
    """Starts a new payment attempt for an unpaid booking: a retry, or a switch to another method.
    Never creates a second booking."""
    permission_classes = [permissions.AllowAny]
    serializer_class = PayRequestSerializer
    throttle_scope = 'payment_attempts'

    def get_throttles(self):
        from rest_framework.throttling import ScopedRateThrottle
        return [ScopedRateThrottle()]

    def post(self, request, booking_id):
        booking = get_booking(booking_id)
        if booking is None:
            return Response({'error': 'Booking not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = PayRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        method, mode = data['paymentMethod'], data.get('mpesaMode', 'stk')
        try:
            phone = ''
            if method == 'mpesa' and mode == 'stk':
                phone = normalize_kenyan_phone(data.get('mpesaPhoneNumber', ''))
            release_expired_holds(booking.gathering_id)
            payment = start_payment(booking, method, mode, phone)
            if payment.provider == 'simulated':
                refresh_payment(payment)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PaymentError as exc:
            booking.refresh_from_db()
            return Response({'error': str(exc), 'booking': booking_status_payload(booking, request)},
                            status=status.HTTP_409_CONFLICT)
        booking.refresh_from_db()
        return Response(booking_status_payload(booking, request))
