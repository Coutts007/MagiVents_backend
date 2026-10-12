"""
Address search for the event editor's map, proxied through the server so that we follow the
OpenStreetMap Nominatim usage policy (operations.osmfoundation.org/policies/nominatim/):
an identifying User-Agent, at most one request per second, and cached results. Searches run only
when the organizer presses Search, never as they type (autocomplete is not allowed).
"""
import hashlib
import logging
import time

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_SECONDS = 7 * 24 * 3600
LOCK_KEY = 'geocode:rate-lock'


class GeocodingError(Exception):
    pass


def _wait_for_slot():
    """Allows one upstream request per second across all workers sharing the cache."""
    for _ in range(20):
        if cache.add(LOCK_KEY, 1, timeout=1):
            return
        time.sleep(0.1)
    raise GeocodingError('Address search is busy. Please try again in a moment.')


def search_places(query, country_codes='ke', limit=5):
    query = ' '.join(query.split())[:200]
    key = 'geocode:' + hashlib.sha256(f'{country_codes}|{limit}|{query.lower()}'.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached

    _wait_for_slot()
    try:
        res = requests.get(
            settings.GEOCODER_URL,
            params={'q': query, 'format': 'jsonv2', 'countrycodes': country_codes, 'limit': limit},
            headers={'User-Agent': settings.GEOCODER_USER_AGENT, 'Accept-Language': 'en'},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Geocoding failed for %r: %s', query, exc)
        raise GeocodingError('Address search is not available right now. Drag the pin to the venue instead.') from exc

    results = [
        {'name': item.get('display_name', ''), 'lat': float(item['lat']), 'lng': float(item['lon'])}
        for item in data if item.get('lat') and item.get('lon')
    ]
    # "Nothing found" is kept only briefly, in case the map data improves
    cache.set(key, results, CACHE_SECONDS if results else 3600)
    return results
