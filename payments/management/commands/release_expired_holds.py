from django.core.management.base import BaseCommand

from payments.services import release_expired_holds


class Command(BaseCommand):
    help = 'Expire unpaid bookings whose hold time has passed and give their tickets back. Run every minute or so.'

    def handle(self, *args, **options):
        released = release_expired_holds()
        self.stdout.write(self.style.SUCCESS(f'Released {released} expired booking holds.'))
