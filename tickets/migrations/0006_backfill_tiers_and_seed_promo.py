from django.db import migrations
from django.db.models import Sum


def forwards(apps, schema_editor):
    TicketTier = apps.get_model('gatherings', 'TicketTier')
    Booking = apps.get_model('tickets', 'Booking')
    PromoCode = apps.get_model('tickets', 'PromoCode')

    # Link existing bookings to their tier (bookings only stored the tier name before)
    for tier in TicketTier.objects.all():
        Booking.objects.filter(gathering_id=tier.gathering_id, tier_name=tier.name, tier__isnull=True).update(tier=tier)

    # A tier's total is what is left plus what was already sold
    for tier in TicketTier.objects.all():
        sold = Booking.objects.filter(tier=tier).aggregate(n=Sum('quantity'))['n'] or 0
        tier.quantity = tier.available + sold
        tier.order = tier.id
        tier.save(update_fields=['quantity', 'order'])

    # The promo code that used to be hard-coded keeps working
    PromoCode.objects.get_or_create(code='KARIBU15', gathering=None, defaults={'percent_off': 15, 'is_active': True})


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0005_booking_tier_promocode'),
        ('gatherings', '0005_alter_tickettier_options_tickettier_order_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
