from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView


@extend_schema(responses=OpenApiTypes.OBJECT)
class PublicConfigView(APIView):
    """Non-secret settings the frontend needs, such as the M-Pesa Paybill number shown at checkout."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        from listings.services import online_payments_available
        from payments.providers import available_methods
        response = Response({
            'mpesaPaybill': settings.MPESA_C2B_SHORTCODE or settings.MPESA_PAYBILL_NUMBER,
            'mpesaAccountName': settings.MPESA_ACCOUNT_NAME,
            'paymentsMode': settings.PAYMENTS_MODE,
            'paymentMethods': available_methods(),
            'paymentHoldMinutes': settings.PAYMENT_HOLD_MINUTES,
            # False in live mode: listing fees are paid by hand and confirmed by an admin
            'listingFeeOnline': online_payments_available(),
        })
        # The same for everyone and loaded on every visit: let nginx and browsers reuse it for a minute
        response['Cache-Control'] = 'public, max-age=60'
        return response


@extend_schema(responses=OpenApiTypes.OBJECT)
class GeocodeView(APIView):
    """Finds coordinates for a venue address (OpenStreetMap Nominatim, cached). Organizers only."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'geocode'

    def get_throttles(self):
        from rest_framework.throttling import ScopedRateThrottle
        return [ScopedRateThrottle()]

    def get(self, request):
        from .geocoding import GeocodingError, search_places
        query = request.query_params.get('q', '').strip()
        if len(query) < 3:
            return Response({'error': 'Type at least 3 characters of the address.'}, status=400)
        try:
            return Response({'results': search_places(query)})
        except GeocodingError as exc:
            return Response({'error': str(exc)}, status=503)


@extend_schema(responses=OpenApiTypes.OBJECT)
class HealthView(APIView):
    """For load balancers and uptime checks: 200 when the database and cache answer, 503 otherwise."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        import uuid

        from django.core.cache import cache
        from django.db import connection

        checks = {}
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            checks['database'] = 'ok'
        except Exception:  # noqa: BLE001 - any failure means unhealthy
            checks['database'] = 'error'
        try:
            key, value = f'health:{uuid.uuid4().hex}', 'ok'
            cache.set(key, value, 10)
            checks['cache'] = 'ok' if cache.get(key) == value else 'error'
            cache.delete(key)
        except Exception:  # noqa: BLE001
            checks['cache'] = 'error'
        healthy = all(v == 'ok' for v in checks.values())
        return Response({'status': 'ok' if healthy else 'error', **checks}, status=200 if healthy else 503)
