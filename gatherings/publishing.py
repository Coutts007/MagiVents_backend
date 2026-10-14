"""
The rules an event must meet before it can be on the site (status published or sold_out).

Every path that can publish goes through `ensure_publishable`: the event API (create, full and partial
update), the listing-fee payment confirmation, the staff console and the admin dashboard. `Gathering.save()`
also calls it whenever a draft is about to become public, as a backstop for any path that was missed.
"""
from rest_framework import status
from rest_framework.exceptions import APIException


class PublishBlocked(APIException):
    """The event cannot be published yet. The response says why, in a form the organizer can act on."""
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'publish_blocked'

    def __init__(self, message, code=None, **extra):
        super().__init__(detail={'error': message, 'code': code or self.default_code, **extra})
        self.message = message


class OrganizerSuspended(PublishBlocked):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = 'organizer_suspended'


def suspended_message():
    return 'Your organizer account is suspended, so you cannot create or publish events. Contact MagiVents for help.'


def sells_paid_tickets(gathering):
    if gathering.is_free:
        return False
    if gathering.starting_price > 0:
        return True
    return gathering.pk is not None and gathering.tiers.filter(price__gt=0).exists()


def ensure_publishable(gathering):
    """Raises PublishBlocked when `gathering` may not go public."""
    if gathering.organizer.is_suspended:
        raise OrganizerSuspended(suspended_message())
    if sells_paid_tickets(gathering):
        from payouts.models import payout_account_for
        if payout_account_for(gathering) is None:
            raise PublishBlocked(
                'Add your payment details before publishing an event with paid tickets, so MagiVents knows where '
                'to pay out your ticket sales. Set them in your profile, or for this event in the event form.',
                code='payment_details_missing',
            )
