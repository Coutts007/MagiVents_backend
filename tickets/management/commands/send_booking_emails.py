import logging
import time

from django.core.mail import get_connection
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from tickets.emails import send_booking_confirmation
from tickets.models import Booking

logger = logging.getLogger(__name__)


def send_due_emails(limit=100):
    """Sends the ticket emails of bookings marked by the API. Returns how many were sent."""
    sent = 0
    due = list(Booking.objects.filter(confirmation_email_due=True).values_list('id', flat=True)[:limit])
    if not due:
        return 0
    # One connection to the mail server for the whole batch, instead of a new (TLS) connection per email. If the
    # server cannot be reached, nothing is claimed and the next run tries again.
    connection = get_connection()
    try:
        connection.open()
    except Exception:  # noqa: BLE001 - any connection failure means "try later"
        logger.exception('Could not connect to the mail server; %s ticket emails wait for the next run', len(due))
        return 0
    try:
        for booking_id in due:
            # Claiming with a conditional update means two senders never email the same booking
            if not Booking.objects.filter(id=booking_id, confirmation_email_due=True).update(confirmation_email_due=False):
                continue
            booking = Booking.objects.select_related('gathering').get(id=booking_id)
            sent += send_booking_confirmation(booking, connection=connection)
    finally:
        connection.close()
    return sent


class Command(BaseCommand):
    help = ('Send the ticket emails of confirmed bookings (used with BOOKING_EMAILS_IN_BACKGROUND=True). '
            'With --loop, keeps running and checks every few seconds.')

    def add_arguments(self, parser):
        parser.add_argument('--loop', type=float, default=0, metavar='SECONDS',
                            help='Keep running, checking for new bookings every SECONDS.')

    def handle(self, *args, loop=0, **options):
        while True:
            sent = send_due_emails()
            if not loop:
                self.stdout.write(self.style.SUCCESS(f'Sent {sent} ticket emails.'))
                return
            close_old_connections()
            if sent == 0:
                time.sleep(loop)
