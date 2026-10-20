"""Booking confirmation email: HTML with a plain-text alternative and the PDF ticket attached."""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .documents import admits_label, build_ticket_pdf, payment_line, ticket_url

logger = logging.getLogger(__name__)


def send_booking_confirmation(booking, connection=None):
    """Sends the ticket to the attendee. Returns False instead of raising, so email never breaks a booking.
    `connection` lets a batch of emails share one connection to the mail server."""
    try:
        g = booking.gathering
        context = {
            'booking': booking,
            'event': g,
            'venue': ', '.join(filter(None, [g.venue_name, g.venue_address, g.venue_city])),
            'payment': payment_line(booking),
            'ticket_url': ticket_url(booking),
            'people': admits_label(booking),
            'contact_email': settings.MAGIVENTS_CONTACT_EMAIL,
        }
        message = EmailMultiAlternatives(
            subject=f'Your MagiVents ticket for {g.title} ({booking.ticket_code})',
            body=render_to_string('tickets/emails/booking_confirmed.txt', context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[booking.attendee_email],
            reply_to=[settings.MAGIVENTS_CONTACT_EMAIL],
            connection=connection,
        )
        message.attach_alternative(render_to_string('tickets/emails/booking_confirmed.html', context), 'text/html')
        message.attach(f'MagiVents-Ticket-{booking.ticket_code}.pdf', build_ticket_pdf(booking), 'application/pdf')
        message.send()
        return True
    except Exception:  # noqa: BLE001 - any provider or rendering failure is logged, never raised
        logger.exception('Could not send the confirmation email for booking %s', booking.id)
        return False
