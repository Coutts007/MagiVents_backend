from decimal import Decimal

from django.db import migrations

USD_TO_KES = Decimal('130')


def forwards(apps, schema_editor):
    Booking = apps.get_model('tickets', 'Booking')
    for booking in Booking.objects.filter(currency='$'):
        booking.unit_price *= USD_TO_KES
        booking.discount *= USD_TO_KES
        booking.total_price *= USD_TO_KES
        booking.total_in_kes = booking.total_price
        booking.currency = 'KES'
        booking.save(update_fields=['unit_price', 'discount', 'total_price', 'total_in_kes', 'currency'])


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0003_alter_booking_currency_alter_booking_payment_method'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
