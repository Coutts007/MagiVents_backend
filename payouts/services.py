from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from accounts.models import User
from tickets.models import Booking
from .models import Payout, PayoutAccount


@transaction.atomic
def record_payout(organizer_id, amount, reference, paid_on, note, recorded_by):
    """Records money the platform sent an organizer. A payout can never exceed the balance outstanding:
    the organizer's row is locked, so two admins recording at once cannot both pay out the same money."""
    organizer = User.objects.select_for_update().get(pk=organizer_id)
    amount = Decimal(amount)
    if amount <= 0 or amount != amount.to_integral_value():
        raise ValidationError({'amount': 'Enter a whole number of shillings above 0.'})
    if paid_on > date.today():
        raise ValidationError({'paidOn': 'The payout date cannot be in the future.'})
    earned = Booking.objects.filter(status='confirmed', gathering__organizer=organizer) \
        .aggregate(t=Sum('total_price'))['t'] or Decimal('0')
    paid = Payout.objects.filter(organizer=organizer).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    balance = earned - paid
    if amount > balance:
        raise ValidationError({'amount': f'This is more than the balance outstanding (KSh {balance:,.0f}).'})
    details = PayoutAccount.objects.filter(organizer=organizer, gathering__isnull=True).first()
    return Payout.objects.create(
        organizer=organizer, amount=amount, reference=reference.strip(), paid_on=paid_on, note=note.strip(),
        sent_to=details.summary() if details else '', recorded_by=recorded_by,
    )
