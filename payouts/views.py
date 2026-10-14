from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PayoutAccount
from .serializers import PayoutAccountSerializer, payout_account_payload, save_payout_account


class IsOrganizerAccount(permissions.BasePermission):
    message = 'Only organizers have payment details. Become an organizer first.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_organizer)


@extend_schema(request=PayoutAccountSerializer, responses=OpenApiTypes.OBJECT)
class MyPayoutDetailsView(APIView):
    """The organizer's default payment details: where MagiVents pays out their ticket money."""
    permission_classes = [IsOrganizerAccount]

    def _current(self, request):
        return PayoutAccount.objects.filter(organizer=request.user, gathering__isnull=True).first()

    def get(self, request):
        return Response({'details': payout_account_payload(self._current(request))})

    def put(self, request):
        serializer = PayoutAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = save_payout_account(request.user, serializer.validated_data, by=request.user)
        return Response({'details': payout_account_payload(account)})

    def delete(self, request):
        PayoutAccount.objects.filter(organizer=request.user, gathering__isnull=True).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def own_gathering_or_403(request, gathering_id):
    from gatherings.models import Gathering
    gathering = Gathering.objects.filter(id=gathering_id).first()
    if gathering is None or gathering.organizer_id != request.user.id:
        # The same answer whether the event does not exist or belongs to someone else
        raise PermissionDenied('Only the organizer of this event can see or change its payment details.')
    return gathering


@extend_schema(request=PayoutAccountSerializer, responses=OpenApiTypes.OBJECT)
class EventPayoutDetailsView(APIView):
    """One event's payment details, overriding the organizer's default. Owner only."""
    permission_classes = [permissions.IsAuthenticated]

    def _payload(self, gathering):
        from .models import payout_account_for
        override = PayoutAccount.objects.filter(gathering=gathering).first()
        return {
            'override': payout_account_payload(override),
            'effective': payout_account_payload(payout_account_for(gathering)),
            'usesDefault': override is None,
        }

    def get(self, request, gathering_id):
        return Response(self._payload(own_gathering_or_403(request, gathering_id)))

    def put(self, request, gathering_id):
        gathering = own_gathering_or_403(request, gathering_id)
        serializer = PayoutAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        save_payout_account(request.user, serializer.validated_data, gathering=gathering, by=request.user)
        return Response(self._payload(gathering))

    def delete(self, request, gathering_id):
        """Goes back to the organizer's default details."""
        gathering = own_gathering_or_403(request, gathering_id)
        PayoutAccount.objects.filter(gathering=gathering).delete()
        return Response(self._payload(gathering))
