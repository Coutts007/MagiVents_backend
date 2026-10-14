from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework import serializers

from core.images import absolute_media_url, data_url_to_file, is_data_url
from .models import AgendaItem, Gathering, TicketTier


class TicketTierSerializer(serializers.ModelSerializer):
    # Frontend tier ids are strings; ids it invents for new tiers (e.g. "tier-standard-123") are ignored
    id = serializers.CharField(required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    perks = serializers.ListField(child=serializers.CharField(allow_blank=True), required=False)
    # Total tickets of this type. Older clients send only `available` (tickets left), which still works.
    quantity = serializers.IntegerField(min_value=0, required=False)
    # People one ticket admits: 1 by default, 2 for a couple, the group size for a group ticket
    admits = serializers.IntegerField(min_value=1, max_value=TicketTier.MAX_ADMITS, required=False)
    # Flash sale (times are ISO 8601; stored in UTC)
    salePrice = serializers.DecimalField(source='sale_price', max_digits=10, decimal_places=2, min_value=1,
                                         required=False, allow_null=True)
    saleStartsAt = serializers.DateTimeField(source='sale_starts_at', required=False, allow_null=True)
    saleEndsAt = serializers.DateTimeField(source='sale_ends_at', required=False, allow_null=True)
    saleQuantity = serializers.IntegerField(source='sale_quantity', min_value=1, required=False, allow_null=True)
    # The tier's own sales window
    onSaleFrom = serializers.DateTimeField(source='on_sale_from', required=False, allow_null=True)
    onSaleUntil = serializers.DateTimeField(source='on_sale_until', required=False, allow_null=True)

    class Meta:
        model = TicketTier
        fields = ['id', 'name', 'price', 'description', 'quantity', 'available', 'perks', 'admits',
                  'salePrice', 'saleStartsAt', 'saleEndsAt', 'saleQuantity', 'onSaleFrom', 'onSaleUntil']
        extra_kwargs = {'description': {'required': False, 'allow_blank': True}, 'available': {'required': False}}

    def validate(self, attrs):
        name = attrs.get('name') or 'This ticket type'
        if attrs.get('sale_price') is not None:
            start, end = attrs.get('sale_starts_at'), attrs.get('sale_ends_at')
            if not start or not end:
                raise serializers.ValidationError(f'"{name}": a flash sale needs a start and an end time.')
            if end <= start:
                raise serializers.ValidationError(f'"{name}": the flash sale must end after it starts.')
            if attrs['sale_price'] != attrs['sale_price'].to_integral_value():
                raise serializers.ValidationError(f'"{name}": the sale price must be whole shillings.')
            if attrs['sale_price'] >= attrs['price']:
                raise serializers.ValidationError(f'"{name}": the sale price must be lower than the normal price.')
            limit, quantity = attrs.get('sale_quantity'), attrs.get('quantity')
            if limit is not None and quantity is not None and limit > quantity:
                raise serializers.ValidationError(f'"{name}": the sale limit cannot be more than the tickets available.')
        elif 'sale_price' in attrs:
            # Removing the sale price ends the sale
            attrs.update(sale_price=None, sale_starts_at=None, sale_ends_at=None, sale_quantity=None)
        start, end = attrs.get('on_sale_from'), attrs.get('on_sale_until')
        if start and end and end <= start:
            raise serializers.ValidationError(f'"{name}": sales must close after they open.')
        return attrs


def tier_payload(tier, now=None):
    """A tier as the frontend's TicketTier type. Prices in effect are decided here, never by the client."""
    now = now or timezone.now()
    current_price, on_sale = tier.price_at(now)
    iso = lambda value: value.isoformat() if value else None  # noqa: E731
    return {
        'id': str(tier.id),
        'name': tier.name,
        'price': float(tier.price),
        'description': tier.description,
        'quantity': tier.quantity,
        'available': tier.available,
        'sold': tier.sold,
        'perks': tier.perks,
        'admits': tier.admits,
        'salePrice': float(tier.sale_price) if tier.sale_price is not None else None,
        'saleStartsAt': iso(tier.sale_starts_at),
        'saleEndsAt': iso(tier.sale_ends_at),
        'saleQuantity': tier.sale_quantity,
        'saleRemaining': tier.sale_remaining,
        'saleActive': on_sale,
        'currentPrice': float(current_price),
        'onSaleFrom': iso(tier.on_sale_from),
        'onSaleUntil': iso(tier.on_sale_until),
        'salesState': tier.sales_state(now),
    }


class AgendaItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgendaItem
        fields = ['time', 'title', 'detail']
        extra_kwargs = {'detail': {'required': False}}


class CoordinatesSerializer(serializers.Serializer):
    lat = serializers.FloatField()
    lng = serializers.FloatField()


class VenueSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    neighborhood = serializers.CharField(max_length=120, required=False, allow_blank=True)
    mapNote = serializers.CharField(required=False, allow_blank=True)
    coordinates = CoordinatesSerializer(required=False, allow_null=True)


class PricingSerializer(serializers.Serializer):
    # Accepted for compatibility but ignored: every price is in KES
    currency = serializers.CharField(max_length=10, required=False)
    startingPrice = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    tiers = TicketTierSerializer(many=True, required=False)


class HostSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    role = serializers.CharField(max_length=120, required=False, allow_blank=True)
    avatarUrl = serializers.CharField(max_length=500, required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)


class GatheringSerializer(serializers.Serializer):
    """Reads and writes the frontend `EventItem` shape (src/types/index.ts)."""
    id = serializers.CharField(read_only=True)
    organizerId = serializers.CharField(source='organizer_id', read_only=True)
    title = serializers.CharField(max_length=255)
    subtitle = serializers.CharField(max_length=300, required=False, allow_blank=True)
    category = serializers.ChoiceField(choices=Gathering.CATEGORY_CHOICES)
    description = serializers.CharField(required=False, allow_blank=True)
    fullContent = serializers.CharField(source='full_content', required=False, allow_blank=True)
    date = serializers.CharField(source='date_display', max_length=120)
    isoDate = serializers.DateField(source='iso_date')
    time = serializers.CharField(source='time_display', max_length=120)
    venue = VenueSerializer()
    isFree = serializers.BooleanField(source='is_free', required=False)
    pricing = PricingSerializer()
    capacity = serializers.IntegerField(min_value=1)
    attendeeCount = serializers.IntegerField(source='attendee_count', read_only=True)
    imageUrl = serializers.CharField(required=False, allow_blank=True)
    host = HostSerializer(required=False)
    agenda = AgendaItemSerializer(many=True, required=False)
    status = serializers.ChoiceField(choices=Gathering.STATUS_CHOICES, required=False)
    isFeatured = serializers.BooleanField(source='is_featured', required=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    curatorNote = serializers.CharField(source='curator_note', required=False, allow_blank=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)

    def validate(self, attrs):
        is_free = attrs.get('is_free', getattr(self.instance, 'is_free', False))
        pricing = attrs.get('pricing')
        if pricing is not None:
            if is_free:
                # Free events cost nothing at every tier, so there is nothing to put on sale either
                pricing['startingPrice'] = 0
                for tier in pricing.get('tiers', []):
                    tier.update(price=0, sale_price=None, sale_starts_at=None, sale_ends_at=None, sale_quantity=None)
            elif pricing['startingPrice'] <= 0:
                raise serializers.ValidationError(
                    {'pricing': 'Enter a ticket price above KSh 0, or mark the event as free.'}
                )
        elif is_free and self.instance is not None:
            # Switching an existing event to free without resending pricing
            attrs['pricing'] = {'startingPrice': 0, 'tiers': [
                {'id': str(t.id), 'name': t.name, 'price': 0, 'description': t.description,
                 'quantity': t.quantity, 'perks': t.perks, 'admits': t.admits,
                 'on_sale_from': t.on_sale_from, 'on_sale_until': t.on_sale_until,
                 'sale_price': None, 'sale_starts_at': None, 'sale_ends_at': None, 'sale_quantity': None}
                for t in self.instance.tiers.all()
            ]}
        elif 'is_free' in attrs and not is_free and self.instance is not None and self.instance.starting_price <= 0:
            raise serializers.ValidationError({'pricing': 'Set a ticket price when an event stops being free.'})

        tiers = (attrs.get('pricing') or {}).get('tiers')
        if tiers:
            if not is_free and any(t['price'] <= 0 for t in tiers):
                raise serializers.ValidationError({'pricing': 'Every ticket type of a paid event needs a price above KSh 0.'})
            if not is_free:
                # The cheapest ticket is the price shown on the event
                attrs['pricing']['startingPrice'] = min(t['price'] for t in tiers)
            if all('quantity' in t for t in tiers):
                # Capacity is the number of people every ticket type admits in total
                existing = {str(t.id): t for t in self.instance.tiers.all()} if self.instance is not None else {}
                attrs['capacity'] = sum(
                    t['quantity'] * t.get('admits', getattr(existing.get(str(t.get('id', ''))), 'admits', 1))
                    for t in tiers
                )
        capacity = attrs.get('capacity')
        if capacity is not None and self.instance is not None and capacity < self.instance.attendee_count:
            raise serializers.ValidationError(
                {'capacity': f'{self.instance.attendee_count} places are already booked; capacity cannot be lower.'}
            )
        return attrs

    def _image_url(self, obj):
        if obj.artwork_image:
            return absolute_media_url(self.context.get('request'), obj.artwork_image)
        return obj.artwork_url

    def _organizer_stats(self, obj):
        """Sales figures from real bookings, shown only to the event's organizer."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated or obj.organizer_id != request.user.id:
            return None
        # The list/detail queryset annotates these; freshly saved instances are aggregated here
        if hasattr(obj, 'revenue_total'):
            revenue, tickets = obj.revenue_total, obj.tickets_sold
        else:
            totals = obj.bookings.filter(status='confirmed').aggregate(revenue=Sum('total_price'), tickets=Sum('quantity'))
            revenue, tickets = totals['revenue'], totals['tickets']
        return {'revenue': float(revenue or 0), 'ticketsSold': int(tickets or 0)}

    def _is_owner(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and obj.organizer_id == request.user.id)

    def _confirmed_attendees(self, obj):
        """People admitted by confirmed bookings (a couple ticket counts 2). Only for the organizer."""
        if not self._is_owner(obj):
            return None
        if hasattr(obj, 'people_confirmed'):
            return int(obj.people_confirmed or 0)
        people = obj.bookings.filter(status='confirmed').aggregate(n=Sum(F('quantity') * F('admits_per_ticket')))['n']
        return int(people or 0)

    def to_representation(self, obj):
        now = timezone.now()
        return {
            'id': str(obj.id),
            'organizerId': str(obj.organizer_id),
            'title': obj.title,
            'subtitle': obj.subtitle,
            'category': obj.category,
            'description': obj.description,
            'fullContent': obj.full_content,
            'date': obj.date_display,
            'isoDate': obj.iso_date.isoformat(),
            'time': obj.time_display,
            'venue': {
                'name': obj.venue_name,
                'address': obj.venue_address,
                'city': obj.venue_city,
                'neighborhood': obj.venue_neighborhood,
                'mapNote': obj.venue_map_note,
                'coordinates': (
                    {'lat': obj.venue_lat, 'lng': obj.venue_lng}
                    if obj.venue_lat is not None and obj.venue_lng is not None else None
                ),
            },
            'isFree': obj.is_free,
            'pricing': {
                'currency': obj.currency,
                'startingPrice': float(obj.starting_price),
                'tiers': [tier_payload(t, now) for t in obj.tiers.all()],
            },
            'capacity': obj.capacity,
            'attendeeCount': obj.attendee_count,
            'imageUrl': self._image_url(obj),
            'host': {
                'name': obj.host_name,
                'role': obj.host_role,
                'avatarUrl': obj.host_avatar_url,
                'bio': obj.host_bio,
            },
            'agenda': [
                {'time': a.time, 'title': a.title, 'detail': a.detail}
                for a in obj.agenda_items.all()
            ],
            'status': obj.status,
            'isFeatured': obj.is_featured,
            'tags': obj.tags,
            'curatorNote': obj.curator_note,
            'createdAt': obj.created_at.isoformat() if obj.created_at else None,
            'organizerStats': self._organizer_stats(obj),
            'confirmedAttendees': self._confirmed_attendees(obj),
            'listingFee': self._listing_fee(obj),
        }

    def _listing_fee(self, obj):
        """The event's listing-fee state (pay to publish). Only for the organizer."""
        if not self._is_owner(obj):
            return None
        from listings.models import ListingFee
        from listings.services import fee_payload, fee_table
        if 'fee_table' not in self.context:
            self.context['fee_table'] = fee_table()
        try:
            fee = obj.listing_fee
        except ListingFee.DoesNotExist:
            return None
        return fee_payload(fee, fee_table=self.context['fee_table'])

    # --- writing -------------------------------------------------------------

    def _apply_fields(self, instance, data):
        """Flatten validated nested EventItem data onto the model instance."""
        venue = data.pop('venue', None)
        pricing = data.pop('pricing', None)
        host = data.pop('host', None)
        image_url = data.pop('imageUrl', None)
        data.pop('agenda', None)

        for field, value in data.items():
            setattr(instance, field, value)

        if venue is not None:
            instance.venue_name = venue['name']
            instance.venue_address = venue.get('address', '')
            instance.venue_city = venue.get('city', '')
            instance.venue_neighborhood = venue.get('neighborhood', '')
            instance.venue_map_note = venue.get('mapNote', '')
            coords = venue.get('coordinates')
            instance.venue_lat = coords['lat'] if coords else None
            instance.venue_lng = coords['lng'] if coords else None

        if pricing is not None:
            instance.starting_price = pricing['startingPrice']
            instance.currency = 'KES'

        if host is not None:
            instance.host_name = host.get('name', '')
            instance.host_role = host.get('role', '')
            instance.host_avatar_url = host.get('avatarUrl', '')
            instance.host_bio = host.get('bio', '')

        if image_url is not None:
            if instance._state.adding or image_url != self._image_url(instance):
                if is_data_url(image_url):
                    instance.artwork_image = data_url_to_file(image_url, 'artwork')
                    instance.artwork_url = ''
                else:
                    if len(image_url) > 500:
                        raise serializers.ValidationError({'imageUrl': 'Image URL is too long (max 500 characters).'})
                    instance.artwork_image = None
                    instance.artwork_url = image_url

    def _sync_tiers(self, instance, tiers):
        existing = {str(t.id): t for t in instance.tiers.all()}
        keep = set()
        for order, tier_data in enumerate(tiers):
            tier_id = str(tier_data.pop('id', '') or '')
            quantity = tier_data.pop('quantity', None)
            available = tier_data.pop('available', None)
            tier = existing.get(tier_id) or TicketTier(gathering=instance, quantity=0, available=0)
            sold = tier.sold if tier.pk else 0
            admits = tier_data.get('admits')
            if tier.pk and sold and admits is not None and admits != tier.admits:
                # Sold tickets already admit their number of people; changing it would break the counts
                raise serializers.ValidationError(
                    {'pricing': f'"{tier.name}" already has tickets sold, so the people each ticket admits '
                                f'cannot change.'}
                )
            if quantity is not None:
                if quantity < sold:
                    raise serializers.ValidationError(
                        {'pricing': f'"{tier_data.get("name", tier.name)}" already has {sold} tickets sold; '
                                    f'its quantity cannot be lower.'}
                    )
                tier.quantity, tier.available = quantity, quantity - sold
            elif available is not None:
                tier.quantity, tier.available = available + sold, available
            elif not tier.pk:
                tier.quantity = tier.available = 50
            for field, value in tier_data.items():
                setattr(tier, field, value)
            tier.order = order
            tier.save()
            keep.add(str(tier.id))
        removed = instance.tiers.exclude(id__in=[int(i) for i in keep])
        sold_out_of = [t.name for t in removed if t.sold > 0]
        if sold_out_of:
            raise serializers.ValidationError(
                {'pricing': f'"{sold_out_of[0]}" already has tickets sold and cannot be removed.'}
            )
        removed.delete()

    def _sync_agenda(self, instance, agenda):
        instance.agenda_items.all().delete()
        AgendaItem.objects.bulk_create([
            AgendaItem(gathering=instance, order=i, **item) for i, item in enumerate(agenda)
        ])

    @transaction.atomic
    def create(self, validated_data):
        tiers = validated_data.get('pricing', {}).get('tiers', [])
        agenda = validated_data.get('agenda', [])
        # Every new event is saved as a draft; publishing is a separate step that checks the listing fee
        validated_data['status'] = 'draft'
        instance = Gathering(organizer=validated_data.pop('organizer'))
        self._apply_fields(instance, validated_data)
        instance.save()
        self._sync_tiers(instance, tiers)
        self._sync_agenda(instance, agenda)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        pricing = validated_data.get('pricing')
        agenda = validated_data.get('agenda')
        self._apply_fields(instance, validated_data)
        instance.save()
        if pricing is not None and 'tiers' in pricing:
            self._sync_tiers(instance, pricing['tiers'])
        if agenda is not None:
            self._sync_agenda(instance, agenda)
        return instance
