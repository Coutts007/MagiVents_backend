"""
Who may use the staff console.

Two locks, both required:
1. the secret path segment from ADMIN_CONSOLE_PATH (it lives only in the server's environment, never in the
   frontend bundle), compared in constant time;
2. a signed-in, active staff account.

Anything else gets the same plain 404 as a URL that does not exist, so the console cannot be discovered.
Wrong attempts are counted per account (or per IP when signed out); after ADMIN_CONSOLE_MAX_FAILURES in an
hour, even the right path answers 404 for that caller.
"""
import hmac
import logging

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission

logger = logging.getLogger(__name__)

MIN_SECRET_LENGTH = 16
LOCKOUT_SECONDS = 3600


def console_enabled():
    return len(settings.ADMIN_CONSOLE_PATH) >= MIN_SECRET_LENGTH


def _caller_key(request):
    if request.user and request.user.is_authenticated:
        return f'console:fail:user:{request.user.pk}'
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '') if settings.BEHIND_PROXY else ''
    ip = forwarded.split(',')[0].strip() or request.META.get('REMOTE_ADDR', '')
    return f'console:fail:ip:{ip}'


def path_matches(secret):
    configured = settings.ADMIN_CONSOLE_PATH
    return console_enabled() and hmac.compare_digest(secret.encode(), configured.encode())


class ConsoleAccess(BasePermission):
    def has_permission(self, request, view):
        key = _caller_key(request)
        if cache.get(key, 0) >= settings.ADMIN_CONSOLE_MAX_FAILURES:
            raise NotFound()
        user = request.user
        allowed = (
            path_matches(view.kwargs.get('secret', ''))
            and user and user.is_authenticated and user.is_active and user.is_staff
        )
        if not allowed:
            if cache.add(key, 1, LOCKOUT_SECONDS) is False:
                cache.incr(key)
            logger.warning('Refused staff console access (%s)', key)
            raise NotFound()
        return True
