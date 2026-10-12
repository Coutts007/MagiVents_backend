from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from gatherings.models import Gathering
from .models import Booking, Bookmark, PromoCode
from .documents import build_ticket_pdf
from .emails import send_booking_confirmation
from .serializers import (
    BookingSerializer,
    PromoCodeSerializer,
    PromoValidateSerializer,
    TicketLookupSerializer,
    normalize_ticket_code,
    promo_discount,
)


class BookingViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                     viewsets.GenericViewSet):
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'guest_bookings'

    def get_permissions(self):
        # Anyone can register for an event; viewing past bookings needs an account
        if self.action == 'create':
            return [permissions.AllowAny()]
        return super().get_permissions()

    def get_throttles(self):
        # Guest bookings are rate limited per IP; signed-in users are not
        if self.action == 'create' and not self.request.user.is_authenticated:
            return [ScopedRateThrottle()]
        return []

    def get_queryset(self):
        # Users can only view their own bookings; "My tickets" lists confirmed ones
        qs = Booking.objects.filter(user=self.request.user).select_related('gathering')
        return qs.filter(status='confirmed') if self.action == 'list' else qs

    def create(self, request, *args, **kwargs):
        from payments.phone import normalize_kenyan_phone
        from payments.serializers import booking_status_payload
        from payments.services import PaymentError, refresh_payment, release_expired_holds, start_payment
        from payments.providers import get_provider

        # Expired holds give their tickets back before availability is checked
        release_expired_holds(request.data.get('eventId'))

        method = request.data.get('paymentMethod') or 'mpesa'
        mode = request.data.get('mpesaMode') or 'stk'
        phone = ''
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            booking = serializer.save()
            if booking.status == 'pending':
                # A booking that cannot even start its payment is rolled back, so no tickets stay held
                try:
                    provider = get_provider(method, mode)
                    if not provider.is_configured():
                        raise PaymentError(provider.not_configured_message)
                    if method == 'mpesa' and mode == 'stk':
                        phone = normalize_kenyan_phone(request.data.get('mpesaPhoneNumber', ''))
                except (PaymentError, ValueError) as exc:
                    transaction.set_rollback(True)
                    return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if booking.status == 'pending':
            try:
                payment = start_payment(booking, method, mode, phone)
                if payment.provider == 'simulated':
                    # The fake gateway may already be done (when no delay is configured)
                    refresh_payment(payment)
            except PaymentError:
                pass  # The booking is marked failed; the payload says why and the attendee can retry
            booking.refresh_from_db()
        return Response(booking_status_payload(booking, request), status=status.HTTP_201_CREATED)


@extend_schema(responses={200: {'type': 'array', 'items': {'type': 'string'}}})
class BookmarkListView(APIView):
    """Returns the ids of the gatherings the current user has bookmarked."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ids = Bookmark.objects.filter(user=request.user).order_by('-created_at').values_list('gathering_id', flat=True)
        return Response([str(i) for i in ids])


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class BookmarkToggleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, gathering_id):
        try:
            gathering = Gathering.objects.get(id=gathering_id)
        except Gathering.DoesNotExist:
            return Response({'error': 'Gathering not found.'}, status=status.HTTP_404_NOT_FOUND)

        bookmark, created = Bookmark.objects.get_or_create(user=request.user, gathering=gathering)
        if not created:
            bookmark.delete()
            return Response({'status': 'unbookmarked'}, status=status.HTTP_200_OK)

        return Response({'status': 'bookmarked'}, status=status.HTTP_201_CREATED)


class PromoCodeViewSet(viewsets.ModelViewSet):
    """Organizers create and manage promo codes for their own events."""
    serializer_class = PromoCodeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PromoCode.objects.filter(gathering__organizer=self.request.user).select_related('gathering')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(responses=OpenApiTypes.OBJECT)
class PromoValidateView(APIView):
    """Checks a code for an event so checkout can show the discount before booking. Nothing is used up."""
    serializer_class = PromoValidateSerializer
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'promo_validate'
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
        serializer = PromoValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        gathering = Gathering.objects.filter(id=data['eventId']).exclude(status='draft').first()
        if gathering is None:
            return Response({'error': 'This event no longer exists.'}, status=status.HTTP_404_NOT_FOUND)

        code = PromoCode.lookup(data['code'], gathering)
        problem = code.problem(gathering) if code else 'This promo code is not valid.'
        if problem:
            return Response({'error': problem}, status=status.HTTP_400_BAD_REQUEST)

        result = {'code': code.code, 'percentOff': code.percent_off}
        tier_id = data.get('tierId', '')
        if data.get('quantity'):
            tier = gathering.tiers.filter(id=int(tier_id)).first() if tier_id.isdigit() else None
            unit = tier.price if tier else gathering.starting_price
            subtotal = unit * data['quantity']
            discount = promo_discount(subtotal, code.percent_off)
            result.update(subtotal=float(subtotal), discount=float(discount), total=float(max(0, subtotal - discount)))
        return Response(result)


# --- Tickets by UUID (the QR code and email link carry the UUID, which is not guessable) -----------

def ticket_response(request, booking):
    return Response(BookingSerializer(booking, context={'request': request}).data)


def find_ticket(booking_id):
    # A ticket exists only once the booking is confirmed (paid, or free)
    return Booking.objects.select_related('gathering').filter(id=str(booking_id), status='confirmed').first()


@extend_schema(responses=BookingSerializer)
class TicketDetailView(APIView):
    """A single ticket, for the link in the QR code and email. Works for guests."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, booking_id):
        booking = find_ticket(booking_id)
        if booking is None:
            return Response({'error': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)
        return ticket_response(request, booking)


@extend_schema(responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
class TicketPdfView(APIView):
    """The ticket as a PDF with its QR code, generated on the server."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, booking_id):
        booking = find_ticket(booking_id)
        if booking is None:
            return Response({'error': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(build_ticket_pdf(booking), content_type='application/pdf')
        disposition = 'inline' if request.query_params.get('inline') else 'attachment'
        response['Content-Disposition'] = f'{disposition}; filename="MagiVents-Ticket-{booking.ticket_code}.pdf"'
        return response


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class TicketResendView(APIView):
    """Sends the confirmation email again, always to the attendee's own address."""
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'ticket_resend'
    throttle_classes = [ScopedRateThrottle]

    def post(self, request, booking_id):
        booking = find_ticket(booking_id)
        if booking is None:
            return Response({'error': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not send_booking_confirmation(booking):
            return Response({'error': 'The email could not be sent right now. Please try again later.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'message': f'Ticket sent to {booking.attendee_email}.'})


class TicketLookupView(APIView):
    """Guests find their ticket with the ticket code and the email they booked with."""
    permission_classes = [permissions.AllowAny]
    serializer_class = TicketLookupSerializer
    throttle_scope = 'ticket_lookup'
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
        serializer = TicketLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = normalize_ticket_code(serializer.validated_data['ticketCode'])
        email = serializer.validated_data['email'].strip()
        booking = Booking.objects.select_related('gathering').filter(
            ticket_code=code, attendee_email__iexact=email, status='confirmed'
        ).first()
        if booking is None:
            # The same answer whether the code or the email was wrong
            return Response({'error': 'We could not find a ticket with that code and email.'},
                            status=status.HTTP_404_NOT_FOUND)
        return ticket_response(request, booking)
