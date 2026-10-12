from django.db.models import Q, Sum
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


class IsOrganizerRoleToCreate(permissions.BasePermission):
    message = 'Only organizers can create events. Become an organizer from your profile first.'

    def has_permission(self, request, view):
        if view.action != 'create':
            return True
        return bool(request.user and request.user.is_authenticated and request.user.is_organizer)


class GatheringViewSet(viewsets.ModelViewSet):
    serializer_class = GatheringSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOrganizerRoleToCreate, IsOrganizerOrReadOnly]
    filter_backends = [filters.SearchFilter]
    search_fields = ['title', 'subtitle', 'category', 'venue_name', 'venue_city', 'host_name', 'tags']

    def get_queryset(self):
        qs = Gathering.objects.prefetch_related('tiers', 'agenda_items').order_by('iso_date').annotate(
            revenue_total=Sum('bookings__total_price'),
            tickets_sold=Sum('bookings__quantity'),
        )
        # Drafts are only visible to their organizer
        visible = ~Q(status='draft')
        if self.request.user.is_authenticated:
            visible |= Q(organizer=self.request.user)
        return qs.filter(visible)

    def perform_create(self, serializer):
        serializer.save(organizer=self.request.user)

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_gatherings(self, request):
        """Returns gatherings organized by the current user."""
        gatherings = self.get_queryset().filter(organizer=request.user)
        serializer = self.get_serializer(gatherings, many=True)
        return Response(serializer.data)
