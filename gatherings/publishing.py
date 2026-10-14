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

    def __init__(self, message, code=None, status_code=None, **extra):
        super().__init__(detail=message, code=code or self.default_code)
        # Kept as plain values, so amounts stay numbers in the JSON response
        self.detail = {'error': message, 'code': code or self.default_code, **extra}
        self.message = message
        if status_code is not None:
            self.status_code = status_code


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


def ensure_publishable(gathering, check_fee=True):
    """Raises PublishBlocked when `gathering` may not go public. The checks run in the order an organizer can
    act on them, so nobody pays a listing fee for an event that still could not be published."""
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
    if check_fee:
        from listings.services import ensure_fee_settled
        ensure_fee_settled(gathering)


def before_save(gathering):
    """Called by Gathering.save(): the backstop for every path that publishes or changes the category."""
    if gathering.is_public and gathering._loaded_status in (None, 'draft'):
        ensure_publishable(gathering)
    previous = gathering._loaded_category
    if previous and gathering.category != previous:
        from listings.services import ensure_category_change_allowed
        ensure_category_change_allowed(gathering, previous)


def after_save(gathering, previous_status, previous_category, created):
    if created or gathering.status != previous_status or gathering.category != previous_category:
        from listings.services import after_gathering_saved
        after_gathering_saved(gathering, previous_status, previous_category)
