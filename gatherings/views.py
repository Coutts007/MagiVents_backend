from django.db.models import Q
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Gathering
from .serializers import GatheringSerializer


class IsOrganizerOrReadOnly(permissions.BasePermission):
    message = 'Only the organizer of this gathering can modify it.'

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.organizer_id == request.user.id


class GatheringViewSet(viewsets.ModelViewSet):
    serializer_class = GatheringSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOrganizerOrReadOnly]
    filter_backends = [filters.SearchFilter]
    search_fields = ['title', 'subtitle', 'venue_city', 'tags']

    def get_queryset(self):
        qs = Gathering.objects.prefetch_related('tiers', 'agenda_items').order_by('iso_date')
        # Drafts are only visible to their organizer
        visible = ~Q(status='draft')
        if self.request.user.is_authenticated:
            visible |= Q(organizer=self.request.user)
        return qs.filter(visible)

    def perform_create(self, serializer):
        serializer.save(organizer=self.request.user)

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_gatherings(self, request):
        """Returns gatherings organized by the current curator."""
        gatherings = self.get_queryset().filter(organizer=request.user)
        serializer = self.get_serializer(gatherings, many=True)
        return Response(serializer.data)
