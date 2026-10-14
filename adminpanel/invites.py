"""The email an admin-created organizer receives, with a link to set their password.

The link is Django's own password-reset link (default_token_generator + /accounts/reset/<uid>/<token>/), so it
expires after PASSWORD_RESET_TIMEOUT and stops working once the password has been set.
"""
import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def set_password_link(request, user):
    path = reverse('password_reset_confirm', kwargs={
        'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': default_token_generator.make_token(user),
    })
    return request.build_absolute_uri(path)


def send_organizer_invite(request, user):
    """Returns False instead of raising, so a mail problem never undoes the account."""
    context = {
        'name': user.first_name or user.email,
        'link': set_password_link(request, user),
        'days': max(1, settings.PASSWORD_RESET_TIMEOUT // 86400),
        'frontend_url': settings.FRONTEND_URL,
        'contact_email': settings.MAGIVENTS_CONTACT_EMAIL,
    }
    try:
        send_mail(
            subject='Your MagiVents organizer account',
            message=render_to_string('adminpanel/emails/organizer_invite.txt', context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception('Could not send the organizer invite to %s', user.email)
        return False
