from django.db import transaction
from rest_framework import serializers

from core.images import absolute_media_url, data_url_to_file, is_data_url
from .models import AgendaItem, Gathering, TicketTier


class TicketTierSerializer(serializers.ModelSerializer):
    # Frontend tier ids are strings; ids it invents for new tiers (e.g. "tier-standard-123") are ignored
    id = serializers.CharField(required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    perks = serializers.ListField(child=serializers.CharField(), required=False)

    class Meta:
        model = TicketTier
        fields = ['id', 'name', 'price', 'description', 'available', 'perks']
        extra_kwargs = {'description': {'required': False}, 'available': {'required': False}}


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
                # Free events cost nothing at every tier
                pricing['startingPrice'] = 0
                for tier in pricing.get('tiers', []):
                    tier['price'] = 0
            elif pricing['startingPrice'] <= 0:
                raise serializers.ValidationError(
                    {'pricing': 'Enter a ticket price above KSh 0, or mark the event as free.'}
                )
        elif is_free and self.instance is not None:
            # Switching an existing event to free without resending pricing
            attrs['pricing'] = {'startingPrice': 0, 'tiers': [
                {'id': str(t.id), 'name': t.name, 'price': 0, 'description': t.description,
                 'available': t.available, 'perks': t.perks}
                for t in self.instance.tiers.all()
            ]}
        elif 'is_free' in attrs and not is_free and self.instance is not None and self.instance.starting_price <= 0:
            raise serializers.ValidationError({'pricing': 'Set a ticket price when an event stops being free.'})
        return attrs

    def _image_url(self, obj):
        if obj.artwork_image:
            return absolute_media_url(self.context.get('request'), obj.artwork_image)
        return obj.artwork_url

    def to_representation(self, obj):
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
                'tiers': [
                    {
                        'id': str(t.id),
                        'name': t.name,
                        'price': float(t.price),
                        'description': t.description,
                        'available': t.available,
                        'perks': t.perks,
                    }
                    for t in obj.tiers.all()
                ],
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
        }

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
        for tier_data in tiers:
            tier_id = str(tier_data.pop('id', '') or '')
            tier = existing.get(tier_id) or TicketTier(gathering=instance)
            for field, value in tier_data.items():
                setattr(tier, field, value)
            tier.save()
            keep.add(str(tier.id))
        instance.tiers.exclude(id__in=[int(i) for i in keep]).delete()

    def _sync_agenda(self, instance, agenda):
        instance.agenda_items.all().delete()
        AgendaItem.objects.bulk_create([
            AgendaItem(gathering=instance, order=i, **item) for i, item in enumerate(agenda)
        ])

    @transaction.atomic
    def create(self, validated_data):
        tiers = validated_data.get('pricing', {}).get('tiers', [])
        agenda = validated_data.get('agenda', [])
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
