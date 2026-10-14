"""Notifying admins in the app and by email (Django's email framework: the console backend by default)."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction

from accounts.models import User
from .models import Notification

logger = logging.getLogger(__name__)


def admins():
    return User.objects.filter(role='admin', is_active=True)


def notify_admins(kind, title, body='', *, dedupe_key, gathering=None, exclude=None):
    """Creates one notification per admin and emails them after the transaction commits.

    Idempotent: an admin who already has a notification with this `dedupe_key` gets nothing more, so a
    repeated payment callback never notifies twice.
    """
    created = []
    for admin in admins().exclude(pk=getattr(exclude, 'pk', None)):
        try:
            with transaction.atomic():
                Notification.objects.create(recipient=admin, kind=kind, title=title[:200], body=body,
                                            gathering=gathering, dedupe_key=dedupe_key[:120])
        except IntegrityError:
            continue  # Already notified about this
        created.append(admin.email)
    if created and settings.ADMIN_NOTIFICATION_EMAILS:
        transaction.on_commit(lambda: _email(created, title, body))
    return len(created)


def _email(recipients, title, body):
    link = f"{settings.FRONTEND_URL.rstrip('/')}/?view=admin"
    try:
        send_mail(
            subject=f'[MagiVents admin] {title}',
            message=f'{body}\n\nOpen the admin dashboard: {link}\n',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
        )
    except Exception:  # noqa: BLE001 - an email problem never breaks the action that caused it
        logger.exception('Could not email admins about: %s', title)
