from decimal import Decimal

from django.db import migrations

# Old categories -> closest new category
CATEGORY_MAP = {
    'Culinary & Wine': 'Arts & Culture',
    'Architecture & Design': 'Arts & Culture',
    'Fine Arts & Craft': 'Arts & Culture',
    'Music & Performance': 'Entertainment & Music',
    'Literature & Thought': 'Education & Career',
    'Gatherings & Salons': 'Social Impact & Community',
}
# Fixed rate the frontend used to show KES equivalents of dollar prices
USD_TO_KES = Decimal('130')


def forwards(apps, schema_editor):
    Gathering = apps.get_model('gatherings', 'Gathering')
    TicketTier = apps.get_model('gatherings', 'TicketTier')

    for old, new in CATEGORY_MAP.items():
        Gathering.objects.filter(category=old).update(category=new)

    for gathering in Gathering.objects.exclude(currency='KES'):
        if gathering.currency == '$':
            gathering.starting_price *= USD_TO_KES
            for tier in TicketTier.objects.filter(gathering=gathering):
                tier.price *= USD_TO_KES
                tier.save(update_fields=['price'])
        gathering.currency = 'KES'
        gathering.save(update_fields=['starting_price', 'currency'])

    Gathering.objects.filter(starting_price=0).update(is_free=True)


class Migration(migrations.Migration):

    dependencies = [
        ('gatherings', '0003_gathering_is_free_alter_gathering_category_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
