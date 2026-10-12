from django.conf import settings
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView


class PublicConfigView(APIView):
    """Non-secret settings the frontend needs, such as the M-Pesa Paybill number shown at checkout."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({
            'mpesaPaybill': settings.MPESA_PAYBILL_NUMBER,
            'mpesaAccountName': settings.MPESA_ACCOUNT_NAME,
        })
