from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from gatherings.models import Gathering
from .models import Booking, Bookmark, PromoCode
from .serializers import BookingSerializer, PromoCodeSerializer, PromoValidateSerializer, promo_discount


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


@extend_schema(responses={200: {'type': 'array', 'items': {'type': 'string'}}})
class BookmarkListView(APIView):
    """Returns the ids of the gatherings the current user has bookmarked."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ids = Bookmark.objects.filter(user=request.user).order_by('-created_at').values_list('gathering_id', flat=True)
        return Response([str(i) for i in ids])


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
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


class PromoCodeViewSet(viewsets.ModelViewSet):
    """Organizers create and manage promo codes for their own events."""
    serializer_class = PromoCodeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PromoCode.objects.filter(gathering__organizer=self.request.user).select_related('gathering')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(responses=OpenApiTypes.OBJECT)
class PromoValidateView(APIView):
    """Checks a code for an event so checkout can show the discount before booking. Nothing is used up."""
    serializer_class = PromoValidateSerializer
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'promo_validate'
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
        serializer = PromoValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        gathering = Gathering.objects.filter(id=data['eventId']).exclude(status='draft').first()
        if gathering is None:
            return Response({'error': 'This event no longer exists.'}, status=status.HTTP_404_NOT_FOUND)

        code = PromoCode.lookup(data['code'], gathering)
        problem = code.problem(gathering) if code else 'This promo code is not valid.'
        if problem:
            return Response({'error': problem}, status=status.HTTP_400_BAD_REQUEST)

        result = {'code': code.code, 'percentOff': code.percent_off}
        tier_id = data.get('tierId', '')
        if data.get('quantity'):
            tier = gathering.tiers.filter(id=int(tier_id)).first() if tier_id.isdigit() else None
            unit = tier.price if tier else gathering.starting_price
            subtotal = unit * data['quantity']
            discount = promo_discount(subtotal, code.percent_off)
            result.update(subtotal=float(subtotal), discount=float(discount), total=float(max(0, subtotal - discount)))
        return Response(result)
