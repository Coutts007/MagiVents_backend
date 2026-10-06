from rest_framework import mixins, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from gatherings.models import Gathering
from .models import Booking, Bookmark
from .serializers import BookingSerializer


class BookingViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                     viewsets.GenericViewSet):
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'guest_bookings'

    def get_permissions(self):
        # Anyone can register for an event; viewing past bookings needs an account
        if self.action == 'create':
            return [permissions.AllowAny()]
        return super().get_permissions()

    def get_throttles(self):
        # Guest bookings are rate limited per IP; signed-in users are not
        if self.action == 'create' and not self.request.user.is_authenticated:
            return [ScopedRateThrottle()]
        return []

    def get_queryset(self):
        # Users can only view their own bookings
        return Booking.objects.filter(user=self.request.user).select_related('gathering')


class BookmarkListView(APIView):
    """Returns the ids of the gatherings the current user has bookmarked."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ids = Bookmark.objects.filter(user=request.user).order_by('-created_at').values_list('gathering_id', flat=True)
        return Response([str(i) for i in ids])


class BookmarkToggleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, gathering_id):
        try:
            gathering = Gathering.objects.get(id=gathering_id)
        except Gathering.DoesNotExist:
            return Response({'error': 'Gathering not found.'}, status=status.HTTP_404_NOT_FOUND)

        bookmark, created = Bookmark.objects.get_or_create(user=request.user, gathering=gathering)
        if not created:
            bookmark.delete()
            return Response({'status': 'unbookmarked'}, status=status.HTTP_200_OK)

        return Response({'status': 'bookmarked'}, status=status.HTTP_201_CREATED)
