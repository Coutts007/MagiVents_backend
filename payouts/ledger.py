"""
What each organizer has earned and is owed.

MagiVents collects every ticket payment (an M-Pesa STK Push pays into the account whose credentials the
platform holds; it cannot be pointed at an organizer's own number or Till), then pays organizers out to the
payment details they gave. Earnings come from confirmed bookings, after discounts; the balance outstanding is
earnings minus the payouts recorded so far.
"""
from django.db.models import DecimalField, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from tickets.models import Booking

CONFIRMED = Q(status='confirmed')


def _sum(queryset, group_field, value_field, output=DecimalField(max_digits=14, decimal_places=2)):
    """A correlated SUM per organizer, so several totals can be annotated without multiplying rows."""
    return Coalesce(
        Subquery(queryset.values(group_field).annotate(total=Sum(value_field)).values('total')[:1], output_field=output),
        Value(0), output_field=output,
    )


def earnings_subquery():
    return _sum(Booking.objects.filter(CONFIRMED, gathering__organizer=OuterRef('pk')), 'gathering__organizer',
                'total_price')


def tickets_subquery():
    return _sum(Booking.objects.filter(CONFIRMED, gathering__organizer=OuterRef('pk')), 'gathering__organizer',
                'quantity', IntegerField())


def payouts_subquery():
    from .models import Payout
    return _sum(Payout.objects.filter(organizer=OuterRef('pk')), 'organizer', 'amount')


def with_balances(users):
    return users.annotate(earnings=earnings_subquery(), payouts_total=payouts_subquery()) \
        .annotate(balance=F('earnings') - F('payouts_total'))


def organizer_totals(user):
    from .models import Payout
    earned = Booking.objects.filter(CONFIRMED, gathering__organizer=user).aggregate(t=Sum('total_price'))['t'] or 0
    paid = Payout.objects.filter(organizer=user).aggregate(t=Sum('amount'))['t'] or 0
    return {'earnings': float(earned), 'payoutsTotal': float(paid), 'balance': float(earned - paid)}


def event_earnings(user):
    """Per event: tickets, people and money from confirmed bookings, after discounts."""
    from gatherings.models import Gathering
    rows = Gathering.objects.filter(organizer=user).annotate(
        earned=Coalesce(Sum('bookings__total_price', filter=Q(bookings__status='confirmed')), Value(0),
                        output_field=DecimalField(max_digits=14, decimal_places=2)),
        tickets=Coalesce(Sum('bookings__quantity', filter=Q(bookings__status='confirmed')), Value(0)),
        people=Coalesce(Sum(F('bookings__quantity') * F('bookings__admits_per_ticket'),
                            filter=Q(bookings__status='confirmed')), Value(0)),
        discounts=Coalesce(Sum('bookings__discount', filter=Q(bookings__status='confirmed')), Value(0),
                           output_field=DecimalField(max_digits=14, decimal_places=2)),
    ).order_by('-iso_date')
    return [{
        'eventId': str(g.id), 'title': g.title, 'date': g.date_display, 'isoDate': g.iso_date.isoformat(),
        'status': g.status, 'ticketsSold': g.tickets, 'people': g.people,
        'earnings': float(g.earned), 'discounts': float(g.discounts),
    } for g in rows]


def outstanding_total():
    """Sum of every organizer's positive balance: money the platform still has to pay out."""
    from accounts.models import User
    owners = User.objects.filter(organized_gatherings__isnull=False).distinct()
    return float(sum(max(u.balance, 0) for u in with_balances(owners)))
