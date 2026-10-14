import secrets

from django.db import migrations

ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def forwards(apps, schema_editor):
    """Every event that exists before listing fees counts as already paid, for its current category."""
    Gathering = apps.get_model('gatherings', 'Gathering')
    ListingFee = apps.get_model('listings', 'ListingFee')
    used = set(ListingFee.objects.values_list('reference', flat=True))
    for gathering in Gathering.objects.filter(listing_fee__isnull=True):
        reference = None
        while reference is None or reference in used:
            reference = 'LF' + ''.join(secrets.choice(ALPHABET) for _ in range(8))
        used.add(reference)
        ListingFee.objects.create(gathering=gathering, reference=reference, status='paid', cleared=True,
                                  cleared_category=gathering.category, grandfathered=True)


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0001_initial'),
        ('gatherings', '0007_tier_types_and_flash_sales'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
