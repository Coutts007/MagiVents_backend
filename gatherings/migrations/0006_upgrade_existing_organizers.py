from django.db import migrations


def forwards(apps, schema_editor):
    # Creating events now needs the organizer role; anyone who already owns an event keeps that ability
    User = apps.get_model('accounts', 'User')
    Gathering = apps.get_model('gatherings', 'Gathering')
    owner_ids = Gathering.objects.values_list('organizer_id', flat=True).distinct()
    User.objects.filter(id__in=owner_ids).update(role='curator')


class Migration(migrations.Migration):

    dependencies = [
        ('gatherings', '0005_alter_tickettier_options_tickettier_order_and_more'),
        ('accounts', '0002_alter_user_role'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
