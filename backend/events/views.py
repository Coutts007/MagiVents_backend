from rest_framework import generics, permissions, viewsets

from .models import Event, TicketTier, User
from .permissions import IsOrganizer
from .serializers import (
    EventSerializer, 
    TicketTierSerializer, 
    UserRegistrationSerializer
)


class UserRegistrationView(generics.CreateAPIView):
    """Public endpoint for new users to create an account."""
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny] # Anyone can access this


class EventViewSet(viewsets.ModelViewSet):
    """CRUD operations for Events."""
    serializer_class = EventSerializer

    def get_permissions(self):
        """
        Organizers can Create/Update/Delete. 
        Any authenticated user (Attendees) can Read (GET).
        """
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsOrganizer()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        """
        Organizers see their own events (including drafts).
        Attendees only see published events.
        """
        user = self.request.user
        if user.role == 'ORGANIZER':
            return Event.objects.filter(organizer=user)
        return Event.objects.filter(status='PUBLISHED')

    def perform_create(self, serializer):
        """Automatically set the organizer to the user making the request."""
        serializer.save(organizer=self.request.user)


class TicketTierViewSet(viewsets.ModelViewSet):
    """CRUD operations for Event Tickets."""
    serializer_class = TicketTierSerializer

    def get_permissions(self):
        # Only Organizers can manage tickets, anyone authenticated can view them
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsOrganizer()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        # Organizers see their own tickets, attendees see tickets for published events
        user = self.request.user
        if user.role == 'ORGANIZER':
            return TicketTier.objects.filter(event__organizer=user)
        return TicketTier.objects.filter(event__status='PUBLISHED')