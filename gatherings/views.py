import re

from django.db import transaction
from django.db.models import F, Prefetch, Q, Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from payments.services import release_expired_holds_soon
from tickets.models import Booking
from tickets.serializers import CheckInSerializer, attendee_row, normalize_ticket_code

from . import catalog
from .models import Gathering, tiers_with_tickets_held
from .publishing import OrganizerSuspended, suspended_message
from .serializers import GatheringSerializer

UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)


def find_booking_for_check_in(value):
    """A booking from what the door staff scanned or typed: the QR URL, a UUID, or the ticket code."""
    match = UUID_RE.search(value)
    bookings = Booking.objects.select_for_update().select_related('gathering')
    if match:
        return bookings.filter(id=match.group(0).lower()).first()
    return bookings.filter(ticket_code=normalize_ticket_code(value)).first()


class IsOrganizerOrReadOnly(permissions.BasePermission):
    message = 'Only the organizer of this gathering can modify it.'

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.organizer_id == request.user.id


class IsOrganizerRoleToCreate(permissions.BasePermission):
    message = 'Only organizers can create events. Become an organizer from your profile first.'

    def has_permission(self, request, view):
        if view.action != 'create':
            return True
        user = request.user
        if user and user.is_authenticated and user.is_suspended:
            raise OrganizerSuspended(suspended_message())
        return bool(user and user.is_authenticated and user.is_organizer)


class GatheringViewSet(viewsets.ModelViewSet):
    serializer_class = GatheringSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOrganizerRoleToCreate, IsOrganizerOrReadOnly]
    filter_backends = [filters.SearchFilter]
    search_fields = ['title', 'subtitle', 'category', 'venue_name', 'venue_city', 'host_name', 'tags']

    def get_queryset(self):
        # Sales totals are not computed here: they are shown only to an event's organizer, and summing every booking
        # for every visitor made the list slower with each ticket sold. See with_sales() and my_gatherings.
        qs = Gathering.objects.select_related('listing_fee').prefetch_related(Prefetch('tiers', queryset=tiers_with_tickets_held()), 'agenda_items').order_by('iso_date')
        # Drafts are only visible to their organizer
        visible = ~Q(status='draft')
        if self.request.user.is_authenticated:
            visible |= Q(organizer=self.request.user)
        return qs.filter(visible)

    @staticmethod
    def with_sales(qs):
        """Adds the organizer's sales figures; without them the serializer works them out per event."""
        return qs.annotate(
            # Sales count confirmed bookings only, not tickets held by unpaid ones
            revenue_total=Sum('bookings__total_price', filter=Q(bookings__status='confirmed')),
            tickets_sold=Sum('bookings__quantity', filter=Q(bookings__status='confirmed')),
            # A couple or group ticket admits several people
            people_confirmed=Sum(F('bookings__quantity') * F('bookings__admits_per_ticket'),
                                 filter=Q(bookings__status='confirmed')),
        )

    def list(self, request, *args, **kwargs):
        def build():
            # Availability is about to be read: give back tickets held by expired bookings first
            release_expired_holds_soon()
            return super(GatheringViewSet, self).list(request, *args, **kwargs)
        return catalog.cached_response(request, build)

    def retrieve(self, request, *args, **kwargs):
        def build():
            release_expired_holds_soon(kwargs.get('pk'))
            return super(GatheringViewSet, self).retrieve(request, *args, **kwargs)
        return catalog.cached_response(request, build)

    def perform_create(self, serializer):
        gathering = serializer.save(organizer=self.request.user)
        from listings.services import amount_due, get_fee, kes
        from notifications.services import notify_admins
        due = amount_due(get_fee(gathering))
        if due > 0:
            organizer = self.request.user
            notify_admins(
                'listing_fee_due', f'New event awaiting its listing fee: {gathering.title}',
                f'{organizer.first_name or organizer.email} created "{gathering.title}" ({gathering.category}). '
                f'It is a draft until the listing fee of {kes(due)} is paid.',
                dedupe_key=f'listing_fee_due:{gathering.id}', gathering=gathering,
            )

    def perform_destroy(self, instance):
        from payments.models import Payment
        fee_payments = Payment.objects.filter(listing_fee__gathering=instance)
        if fee_payments.filter(status='succeeded').exists():
            # The listing-fee payment is a money record that must be kept
            raise PermissionDenied('This event\'s listing fee has been paid, so it cannot be deleted. Unpublish it instead.')
        with transaction.atomic():
            fee_payments.delete()
            instance.delete()

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_gatherings(self, request):
        """Returns gatherings organized by the current user."""
        gatherings = self.with_sales(self.get_queryset().filter(organizer=request.user))
        serializer = self.get_serializer(gatherings, many=True)
        return Response(serializer.data)

    def _own_gathering(self):
        gathering = self.get_object()
        if gathering.organizer_id != self.request.user.id:
            raise PermissionDenied('Only the organizer of this gathering can see its attendees.')
        return gathering

    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def attendees(self, request, pk=None):
        """Every booking for the organizer's event, with check-in progress."""
        gathering = self._own_gathering()
        bookings = list(gathering.bookings.filter(status='confirmed').order_by('attendee_name'))
        checked_in = [b for b in bookings if b.checked_in_at]
        return Response({
            'counts': {
                'bookings': len(bookings),
                'people': sum(b.people for b in bookings),
                'checkedInBookings': len(checked_in),
                'checkedInPeople': sum(b.people for b in checked_in),
            },
            'attendees': [attendee_row(b) for b in bookings],
        })

    @action(detail=True, methods=['post'], url_path='check-in', permission_classes=[permissions.IsAuthenticated])
    @extend_schema(request=CheckInSerializer, responses=OpenApiTypes.OBJECT)
    def check_in(self, request, pk=None):
        """Checks a ticket in at the door. Only the event's organizer can do this."""
        gathering = self._own_gathering()
        serializer = CheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            booking = find_booking_for_check_in(serializer.validated_data['ticket'])
            if booking is None:
                return Response({'result': 'not_found', 'message': 'No ticket matches that code.'},
                                status=status.HTTP_404_NOT_FOUND)
            if booking.gathering_id != gathering.id:
                # Say nothing about the other event's attendee
                return Response({'result': 'wrong_event', 'message': 'This ticket is for a different event.'},
                                status=status.HTTP_409_CONFLICT)
            if booking.status != 'confirmed':
                return Response({'result': 'not_paid', 'message': 'This booking has not been paid, so it has no ticket.'},
                                status=status.HTTP_409_CONFLICT)
            if booking.checked_in_at:
                return Response({'result': 'already_checked_in', 'message': 'This ticket was already checked in.',
                                 'attendee': attendee_row(booking)}, status=status.HTTP_409_CONFLICT)
            booking.checked_in_at = timezone.now()
            booking.checked_in_by = request.user
            booking.save(update_fields=['checked_in_at', 'checked_in_by'])
        return Response({'result': 'checked_in', 'message': f'Welcome, {booking.attendee_name}.',
                         'attendee': attendee_row(booking)})
