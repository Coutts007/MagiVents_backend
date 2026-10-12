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
