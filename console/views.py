"""Staff console API: platform totals, organizers, payments (with the refund queue), bookings and an audit log."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from gatherings.models import Gathering
from payments.models import Payment
from tickets.models import Booking
from .access import ConsoleAccess
from .models import ConsoleAction

NAIROBI = ZoneInfo('Africa/Nairobi')
PAGE_SIZE = 25
CONFIRMED = Q(status='confirmed')


def money(value):
    return float(value or 0)


def paginate(request, queryset, serialize):
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except ValueError:
        page = 1
    count = queryset.count()
    start = (page - 1) * PAGE_SIZE
    return {
        'count': count,
        'page': page,
        'pageSize': PAGE_SIZE,
        'results': [serialize(item) for item in queryset[start:start + PAGE_SIZE]],
    }


def log_action(request, action, target_type, target_id, label='', note=''):
    ConsoleAction.objects.create(staff=request.user, action=action, target_type=target_type,
                                 target_id=str(target_id), target_label=label[:255], note=note)


class ConsoleView(APIView):
    permission_classes = [ConsoleAccess]
    # Keep the console out of the public API schema
    schema = None


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


# --- Session ---------------------------------------------------------------------------------------

class SessionView(ConsoleView):
    """Lets the frontend know the current path is the console and the user may see it."""

    def get(self, request, secret):
        return Response({'staff': {'name': request.user.first_name or request.user.email, 'email': request.user.email}})


# --- Overview --------------------------------------------------------------------------------------

class OverviewView(ConsoleView):
    def get(self, request, secret):
        now = timezone.now()
        today_start = now.astimezone(NAIROBI).replace(hour=0, minute=0, second=0, microsecond=0)
        confirmed = Booking.objects.filter(CONFIRMED)

        def revenue(qs):
            totals = qs.aggregate(revenue=Sum('total_price'), tickets=Sum('quantity'), bookings=Count('id'))
            return {'revenue': money(totals['revenue']), 'tickets': totals['tickets'] or 0, 'bookings': totals['bookings']}

        # Daily confirmed revenue for the last 30 days (Nairobi days), with empty days filled in
        since = (today_start - timedelta(days=29))
        daily = {
            row['day']: row for row in confirmed.filter(confirmed_at__gte=since)
            .annotate(day=TruncDate('confirmed_at', tzinfo=NAIROBI)).values('day')
            .annotate(revenue=Sum('total_price'), tickets=Sum('quantity')).order_by('day')
        }
        days = []
        for offset in range(30):
            day = (since + timedelta(days=offset)).date()
            row = daily.get(day, {})
            days.append({'date': day.isoformat(), 'revenue': money(row.get('revenue')), 'tickets': row.get('tickets') or 0})

        refunds = Payment.booking_payments.filter(needs_refund=True, refund_resolved_at__isnull=True)
        refund_totals = refunds.aggregate(count=Count('id'), amount=Sum('amount'))
        pending = Booking.objects.filter(status='pending').aggregate(count=Count('id'), tickets=Sum('quantity'))

        top = (
            User.objects.filter(role='curator')
            .annotate(revenue=Sum('organized_gatherings__bookings__total_price',
                                  filter=Q(organized_gatherings__bookings__status='confirmed')))
            .filter(revenue__gt=0).order_by('-revenue')[:5]
        )

        return Response({
            'sales': {
                'allTime': revenue(confirmed),
                'last30Days': revenue(confirmed.filter(confirmed_at__gte=since)),
                'today': revenue(confirmed.filter(confirmed_at__gte=today_start)),
            },
            'byMethod': [
                {'method': row['payment_method'], 'revenue': money(row['revenue']), 'bookings': row['bookings']}
                for row in confirmed.values('payment_method').annotate(revenue=Sum('total_price'), bookings=Count('id'))
                .order_by('-revenue')
            ],
            'daily': days,
            'bookingsByStatus': {
                row['status']: row['n'] for row in Booking.objects.values('status').annotate(n=Count('id'))
            },
            'pendingHolds': {'bookings': pending['count'], 'tickets': pending['tickets'] or 0},
            'refundsOpen': {'count': refund_totals['count'], 'amount': money(refund_totals['amount'])},
            'paymentsFailedLast7Days': Payment.booking_payments.filter(status='failed', created_at__gte=now - timedelta(days=7)).count(),
            'users': {
                'total': User.objects.count(),
                'organizers': User.objects.filter(role='curator').count(),
                'newLast30Days': User.objects.filter(date_joined__gte=since).count(),
            },
            'events': {row['status']: row['n'] for row in Gathering.objects.values('status').annotate(n=Count('id'))},
            'topOrganizers': [{'id': str(u.id), 'name': u.first_name or u.email, 'revenue': money(u.revenue)} for u in top],
            'paymentsMode': settings.PAYMENTS_MODE,
        })


# --- Organizers ------------------------------------------------------------------------------------

def organizer_queryset():
    confirmed = Q(organized_gatherings__bookings__status='confirmed')
    return User.objects.annotate(
        events_count=Count('organized_gatherings', distinct=True),
        published_count=Count('organized_gatherings', filter=Q(organized_gatherings__status='published'), distinct=True),
        tickets_sold=Sum('organized_gatherings__bookings__quantity', filter=confirmed),
        revenue=Sum('organized_gatherings__bookings__total_price', filter=confirmed),
    )


def organizer_row(user):
    return {
        'id': str(user.id),
        'name': user.first_name or user.email.split('@')[0],
        'email': user.email,
        'role': user.role,
        'isActive': user.is_active,
        'isStaff': user.is_staff,
        'joinedDate': user.date_joined.isoformat(),
        'lastLogin': user.last_login.isoformat() if user.last_login else None,
        'eventsCount': getattr(user, 'events_count', 0),
        'publishedCount': getattr(user, 'published_count', 0),
        'ticketsSold': getattr(user, 'tickets_sold', None) or 0,
        'revenue': money(getattr(user, 'revenue', 0)),
    }


class OrganizerListView(ConsoleView):
    def get(self, request, secret):
        qs = organizer_queryset()
        # Organizers, plus anyone who still owns events after their role was revoked
        scope = request.query_params.get('scope', 'organizers')
        if scope == 'organizers':
            qs = qs.filter(Q(role='curator') | Q(events_count__gt=0))
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(email__icontains=search) | Q(first_name__icontains=search))
        sort = {'revenue': '-revenue', 'events': '-events_count', 'joined': '-date_joined', 'name': 'first_name'}
        qs = qs.order_by(sort.get(request.query_params.get('sort'), '-revenue'), 'email')
        return Response(paginate(request, qs, organizer_row))


def event_rows(gatherings):
    rows = []
    for g in gatherings.annotate(
        revenue=Sum('bookings__total_price', filter=Q(bookings__status='confirmed')),
        tickets_sold=Sum('bookings__quantity', filter=Q(bookings__status='confirmed')),
        held=Sum('bookings__quantity', filter=Q(bookings__status='pending')),
    ).order_by('-iso_date'):
        rows.append({
            'id': str(g.id), 'title': g.title, 'date': g.date_display, 'isoDate': g.iso_date.isoformat(),
            'city': g.venue_city, 'status': g.status, 'isFree': g.is_free, 'capacity': g.capacity,
            'ticketsSold': g.tickets_sold or 0, 'held': g.held or 0, 'revenue': money(g.revenue),
        })
    return rows


class OrganizerDetailView(ConsoleView):
    def get(self, request, secret, user_id):
        user = organizer_queryset().filter(id=user_id).first()
        if user is None:
            raise NotFound()
        refunds = Payment.booking_payments.filter(booking__gathering__organizer=user, needs_refund=True,
                                         refund_resolved_at__isnull=True).count()
        return Response({
            **organizer_row(user),
            'city': user.city,
            'openRefunds': refunds,
            'events': event_rows(Gathering.objects.filter(organizer=user)),
        })


class RoleSerializer(NoteSerializer):
    role = serializers.ChoiceField(choices=['patron', 'curator'])


class OrganizerRoleView(ConsoleView):
    """Grants or revokes the organizer role. Revoking stops new events; existing ones stay as they are."""

    @extend_schema(request=RoleSerializer)
    def post(self, request, secret, user_id):
        user = User.objects.filter(id=user_id).first()
        if user is None:
            raise NotFound()
        data = RoleSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        role = data.validated_data['role']
        if user.role != role:
            user.role = role
            user.save(update_fields=['role'])
            log_action(request, 'grant_organizer' if role == 'curator' else 'revoke_organizer', 'user', user.id,
                       user.email, data.validated_data.get('note', ''))
        return Response(organizer_row(organizer_queryset().get(id=user.id)))


class EventStatusSerializer(NoteSerializer):
    status = serializers.ChoiceField(choices=['draft', 'published'])


class EventStatusView(ConsoleView):
    """Takes an event off the site (draft) or puts it back."""

    @extend_schema(request=EventStatusSerializer)
    def post(self, request, secret, event_id):
        data = EventStatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            gathering = Gathering.objects.select_for_update().filter(id=event_id).first()
            if gathering is None:
                raise NotFound()
            new_status = data.validated_data['status']
            if new_status == 'published' and gathering.attendee_count >= gathering.capacity:
                new_status = 'sold_out'
            if gathering.status != new_status:
                gathering.status = new_status
                gathering.save(update_fields=['status'])
                log_action(request, 'unpublish_event' if new_status == 'draft' else 'publish_event', 'event',
                           gathering.id, gathering.title, data.validated_data.get('note', ''))
        return Response(event_rows(Gathering.objects.filter(id=gathering.id))[0])


# --- Payments --------------------------------------------------------------------------------------

def payment_row(p):
    b, g = p.booking, p.booking.gathering
    return {
        'id': str(p.id),
        'reference': p.reference,
        'provider': p.provider,
        'method': p.method,
        'status': p.status,
        'amount': money(p.amount),
        'receiptNumber': p.receipt_number or None,
        'providerReference': p.provider_reference or None,
        'phoneNumber': p.phone_number or None,
        'failureReason': p.failure_reason or None,
        'needsRefund': p.needs_refund,
        'refundResolvedAt': p.refund_resolved_at.isoformat() if p.refund_resolved_at else None,
        'refundResolvedBy': (p.refund_resolved_by.email if p.refund_resolved_by else None),
        'refundNote': p.refund_note or None,
        'createdAt': p.created_at.isoformat(),
        'completedAt': p.completed_at.isoformat() if p.completed_at else None,
        'booking': {
            'id': str(b.id), 'status': b.status, 'ticketCode': b.ticket_code, 'attendeeName': b.attendee_name,
            'attendeeEmail': b.attendee_email, 'quantity': b.quantity, 'totalPrice': money(b.total_price),
        },
        'event': {'id': str(g.id), 'title': g.title, 'organizerEmail': g.organizer.email},
    }


class PaymentListView(ConsoleView):
    def get(self, request, secret):
        qs = Payment.booking_payments.select_related('booking__gathering__organizer', 'refund_resolved_by').order_by('-created_at')
        params = request.query_params
        if params.get('status') in ('pending', 'succeeded', 'failed'):
            qs = qs.filter(status=params['status'])
        if params.get('provider') in dict(Payment.PROVIDER_CHOICES):
            qs = qs.filter(provider=params['provider'])
        refund = params.get('refund')
        if refund == 'open':
            qs = qs.filter(needs_refund=True, refund_resolved_at__isnull=True)
        elif refund == 'resolved':
            qs = qs.filter(needs_refund=True, refund_resolved_at__isnull=False)
        search = params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(reference__icontains=search) | Q(receipt_number__icontains=search)
                           | Q(provider_reference__icontains=search) | Q(booking__attendee_email__icontains=search)
                           | Q(booking__ticket_code__iexact=search))
        return Response(paginate(request, qs, payment_row))


class PaymentDetailView(ConsoleView):
    def get(self, request, secret, payment_id):
        payment = Payment.booking_payments.select_related('booking__gathering__organizer', 'refund_resolved_by').filter(
            id=payment_id).first()
        if payment is None:
            raise NotFound()
        return Response({**payment_row(payment), 'rawPayloads': payment.raw_payloads})


class ResolveRefundView(ConsoleView):
    """Records that a flagged payment has been refunded (outside MagiVents) or otherwise settled."""

    @extend_schema(request=NoteSerializer)
    def post(self, request, secret, payment_id):
        data = NoteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        note = data.validated_data.get('note', '').strip()
        if not note:
            raise ValidationError({'note': 'Say how it was settled, e.g. "Reversed on M-Pesa, ref QX12…".'})
        with transaction.atomic():
            payment = Payment.booking_payments.select_for_update().filter(id=payment_id).first()
            if payment is None:
                raise NotFound()
            if not payment.needs_refund:
                raise ValidationError({'detail': 'This payment is not waiting for a refund.'})
            if payment.refund_resolved_at:
                raise ValidationError({'detail': 'This refund was already marked as settled.'})
            payment.refund_resolved_at = timezone.now()
            payment.refund_resolved_by = request.user
            payment.refund_note = note
            payment.save(update_fields=['refund_resolved_at', 'refund_resolved_by', 'refund_note', 'updated_at'])
            log_action(request, 'resolve_refund', 'payment', payment.id, payment.reference, note)
        payment = Payment.booking_payments.select_related('booking__gathering__organizer', 'refund_resolved_by').get(id=payment.id)
        return Response(payment_row(payment), status=status.HTTP_200_OK)


# --- Bookings and audit log -----------------------------------------------------------------------

def booking_row(b):
    return {
        'id': str(b.id), 'ticketCode': b.ticket_code, 'status': b.status, 'attendeeName': b.attendee_name,
        'attendeeEmail': b.attendee_email, 'isGuest': b.user_id is None, 'tierName': b.tier_name,
        'quantity': b.quantity, 'totalPrice': money(b.total_price), 'paymentMethod': b.payment_method,
        'bookingDate': b.booking_date.isoformat(), 'checkedInAt': b.checked_in_at.isoformat() if b.checked_in_at else None,
        'event': {'id': str(b.gathering_id), 'title': b.gathering.title},
    }


class BookingListView(ConsoleView):
    def get(self, request, secret):
        qs = Booking.objects.select_related('gathering').order_by('-booking_date')
        params = request.query_params
        if params.get('status') in ('pending', 'confirmed', 'failed', 'expired'):
            qs = qs.filter(status=params['status'])
        if params.get('eventId'):
            qs = qs.filter(gathering_id=params['eventId'])
        search = params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(ticket_code__iexact=search) | Q(attendee_email__icontains=search)
                           | Q(attendee_name__icontains=search) | Q(gathering__title__icontains=search))
        return Response(paginate(request, qs, booking_row))


class ActionLogView(ConsoleView):
    def get(self, request, secret):
        qs = ConsoleAction.objects.select_related('staff')
        return Response(paginate(request, qs, lambda a: {
            'id': a.id, 'action': a.action, 'targetType': a.target_type, 'targetId': a.target_id,
            'targetLabel': a.target_label, 'note': a.note, 'staff': a.staff.email if a.staff else None,
            'createdAt': a.created_at.isoformat(),
        }))
