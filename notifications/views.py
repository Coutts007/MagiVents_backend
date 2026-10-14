from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification


def notification_row(n):
    return {
        'id': n.id,
        'kind': n.kind,
        'title': n.title,
        'body': n.body,
        'eventId': n.gathering_id,
        'createdAt': n.created_at.isoformat(),
        'readAt': n.read_at.isoformat() if n.read_at else None,
    }


@extend_schema(responses=OpenApiTypes.OBJECT)
class NotificationListView(APIView):
    """The signed-in user's latest notifications and unread count. The navbar bell polls this."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        mine = Notification.objects.filter(recipient=request.user)
        try:
            limit = max(1, min(50, int(request.query_params.get('limit', 20))))
        except ValueError:
            limit = 20
        return Response({
            'unreadCount': mine.filter(read_at__isnull=True).count(),
            'results': [notification_row(n) for n in mine[:limit]],
        })


class MarkReadSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    all = serializers.BooleanField(required=False, default=False)


@extend_schema(request=MarkReadSerializer, responses=OpenApiTypes.OBJECT)
class NotificationReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = MarkReadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        unread = Notification.objects.filter(recipient=request.user, read_at__isnull=True)
        if not data.validated_data['all']:
            unread = unread.filter(id__in=data.validated_data.get('ids', []))
        unread.update(read_at=timezone.now())
        return Response({'unreadCount': Notification.objects.filter(recipient=request.user, read_at__isnull=True).count()})
