"""
Shared cache of the public event catalogue: the event list and event pages as most people see them.

Almost every visit starts by loading the whole event list, so building it for each visitor was the main cost under
load. The rendered JSON (plain and gzipped) is cached for CATALOG_CACHE_SECONDS and shared by everyone who sees the
public version: visitors who are not signed in and signed-in attendees. Organizers and admins always get a fresh
response, because it includes their own drafts and sales figures.

Ticket availability shown in the list can lag by up to CATALOG_CACHE_SECONDS; a booking always checks availability
under row locks. Edits to events, tiers or agendas start a new cache version at once (see gatherings.signals).
"""
import gzip
import hashlib
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.renderers import JSONRenderer

VERSION_KEY = 'catalog:version'
# Once an entry is stale, one request rebuilds it while the others keep serving the stale copy
STALE_FACTOR = 6


def enabled():
    return settings.CATALOG_CACHE_SECONDS > 0


def is_public_view(request):
    """True when the response holds nothing personal: no drafts, no sales figures, no listing-fee state."""
    user = request.user
    return not user.is_authenticated or user.role == 'patron'


def _version():
    version = cache.get(VERSION_KEY)
    if version is None:
        cache.add(VERSION_KEY, 1, None)
        version = cache.get(VERSION_KEY) or 1
    return version


def invalidate():
    """Starts a new cache version, so the next request builds a fresh list."""
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        cache.set(VERSION_KEY, 1, None)


def _key(request):
    # The host is part of the key because media URLs in the payload are absolute
    raw = f'{request.get_host()}|{request.get_full_path()}'
    return f'catalog:{_version()}:{hashlib.sha1(raw.encode()).hexdigest()}'


def _respond(request, body, body_gz):
    accepts_gzip = 'gzip' in request.META.get('HTTP_ACCEPT_ENCODING', '')
    response = HttpResponse(body_gz if accepts_gzip else body, content_type='application/json')
    if accepts_gzip:
        response['Content-Encoding'] = 'gzip'
    response['Vary'] = 'Accept-Encoding, Authorization'
    if not request.user.is_authenticated:
        # Lets the proxy in front (nginx) and browsers reuse the response for anonymous visitors
        response['Cache-Control'] = f'public, max-age={settings.CATALOG_CACHE_SECONDS}'
    return response


def cached_response(request, build):
    """Serves `build()` (a DRF Response) from the shared cache when the request sees the public catalogue."""
    if not (enabled() and is_public_view(request)):
        return build()

    key = _key(request)
    entry = cache.get(key)
    now = time.time()
    if entry is not None:
        fresh_until, body, body_gz = entry
        if fresh_until > now or not cache.add(f'{key}:lock', 1, 30):
            return _respond(request, body, body_gz)

    try:
        response = build()
        if response.status_code != 200:
            return response
        body = JSONRenderer().render(response.data)
        body_gz = gzip.compress(body, compresslevel=6)
        ttl = settings.CATALOG_CACHE_SECONDS
        cache.set(key, (now + ttl, body, body_gz), ttl * STALE_FACTOR)
    finally:
        cache.delete(f'{key}:lock')
    return _respond(request, body, body_gz)
