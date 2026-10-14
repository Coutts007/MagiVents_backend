from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Gathering = apps.get_model('gatherings', 'Gathering')
    # Staff and superusers become admins (the role and is_staff are kept in sync from now on)
    User.objects.filter(is_staff=True).update(role='admin')
    User.objects.filter(is_superuser=True).update(role='admin', is_staff=True)
    # Creating events needs the organizer role: anyone who already owns an event keeps that ability
    owner_ids = Gathering.objects.values_list('organizer_id', flat=True).distinct()
    User.objects.filter(id__in=owner_ids, role='patron').update(role='curator')


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_admin_role_and_suspension'),
        ('gatherings', '0006_upgrade_existing_organizers'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
