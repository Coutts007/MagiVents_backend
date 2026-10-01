import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from gatherings.models import Gathering
from gatherings.serializers import GatheringSerializer

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures' / 'seed_events.json'
SEED_ORGANIZER_EMAIL = 'curator@magivents.local'


class Command(BaseCommand):
    help = 'Load the demo gatherings (exported from the frontend mock data) owned by a seed curator account.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete and recreate gatherings that already exist.')

    @transaction.atomic
    def handle(self, *args, reset=False, **options):
        organizer, created = User.objects.get_or_create(
            email=SEED_ORGANIZER_EMAIL,
            defaults={'username': SEED_ORGANIZER_EMAIL, 'first_name': 'MagiVents Curators', 'role': 'curator'},
        )
        if created:
            organizer.set_unusable_password()
            organizer.save()

        added = skipped = 0
        for event in json.loads(FIXTURE.read_text()):
            existing = Gathering.objects.filter(id=event['id']).first()
            if existing and not reset:
                skipped += 1
                continue
            if existing:
                existing.delete()

            serializer = GatheringSerializer(data=event)
            if not serializer.is_valid():
                raise CommandError(f"{event['id']}: {serializer.errors}")
            gathering = serializer.save(organizer=organizer, id=event['id'])
            gathering.attendee_count = event.get('attendeeCount', 0)
            gathering.save(update_fields=['attendee_count'])
            added += 1

        if options.get('verbosity', 1):
            self.stdout.write(self.style.SUCCESS(f'Seeded {added} gatherings ({skipped} already present).'))
