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
        from payments.providers import available_methods
        return Response({
            'mpesaPaybill': settings.MPESA_C2B_SHORTCODE or settings.MPESA_PAYBILL_NUMBER,
            'mpesaAccountName': settings.MPESA_ACCOUNT_NAME,
            'paymentsMode': settings.PAYMENTS_MODE,
            'paymentMethods': available_methods(),
            'paymentHoldMinutes': settings.PAYMENT_HOLD_MINUTES,
        })


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
