import random
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from rest_framework import serializers

from gatherings.models import Gathering, TicketTier
from .models import Booking

# Mirrors the codes accepted by the checkout form in the frontend
PROMO_CODES = {'MAGISAND': Decimal('0.15'), 'PATRON15': Decimal('0.15')}


def generate_ticket_code():
    while True:
        code = f'MV-{random.randint(100000, 999999)}'
        if not Booking.objects.filter(ticket_code=code).exists():
            return code


class BookingSerializer(serializers.ModelSerializer):
    """Reads/writes the frontend `TicketBooking` shape. Prices are always computed server-side."""
    id = serializers.CharField(read_only=True)
    eventId = serializers.CharField(write_only=True)
    tierId = serializers.CharField(write_only=True, required=False, allow_blank=True)
    tierName = serializers.CharField(source='tier_name', required=False, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1, max_value=20)
    attendeeName = serializers.CharField(source='attendee_name', max_length=120)
    attendeeEmail = serializers.EmailField(source='attendee_email')
    paymentMethod = serializers.ChoiceField(source='payment_method', choices=Booking.PAYMENT_CHOICES, required=False)
    mpesaPhoneNumber = serializers.CharField(source='mpesa_phone_number', required=False, allow_blank=True, max_length=30)
    mpesaReceiptNumber = serializers.CharField(source='mpesa_receipt_number', required=False, allow_blank=True, max_length=50)
    mpesaMode = serializers.ChoiceField(source='mpesa_mode', choices=['stk', 'paybill'], required=False, allow_blank=True)
    totalInKes = serializers.DecimalField(source='total_in_kes', max_digits=12, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    promoCode = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Booking
        fields = [
            'id', 'eventId', 'tierId', 'tierName', 'quantity', 'attendeeName', 'attendeeEmail',
            'paymentMethod', 'mpesaPhoneNumber', 'mpesaReceiptNumber', 'mpesaMode', 'totalInKes',
            'notes', 'promoCode',
        ]

    def to_representation(self, obj):
        g = obj.gathering
        return {
            'id': str(obj.id),
            'eventId': str(g.id),
            'eventTitle': g.title,
            'eventDate': g.date_display,
            'eventTime': g.time_display,
            'venueName': g.venue_name,
            'tierName': obj.tier_name,
            'quantity': obj.quantity,
            'unitPrice': float(obj.unit_price),
            'totalPrice': float(obj.total_price),
            'attendeeName': obj.attendee_name,
            'attendeeEmail': obj.attendee_email,
            'bookingDate': obj.booking_date.strftime('%b %d, %Y').replace(' 0', ' '),
            'ticketCode': obj.ticket_code,
            'paymentMethod': obj.payment_method,
            'mpesaPhoneNumber': obj.mpesa_phone_number or None,
            'mpesaReceiptNumber': obj.mpesa_receipt_number or None,
            'mpesaMode': obj.mpesa_mode or None,
            'currency': obj.currency,
            'totalInKes': float(obj.total_in_kes) if obj.total_in_kes is not None else None,
            'notes': obj.notes or None,
        }

    def validate_promoCode(self, value):
        value = value.strip().upper()
        if value and value not in PROMO_CODES:
            raise serializers.ValidationError('Invalid promotion code.')
        return value

    @transaction.atomic
    def create(self, validated_data):
        event_id = validated_data.pop('eventId')
        tier_id = validated_data.pop('tierId', '')
        promo = validated_data.pop('promoCode', '')
        requested_tier_name = validated_data.pop('tier_name', '')
        quantity = validated_data['quantity']

        try:
            gathering = Gathering.objects.select_for_update().get(id=event_id)
        except Gathering.DoesNotExist:
            raise serializers.ValidationError({'eventId': 'This gathering no longer exists.'})

        if gathering.status != 'published':
            raise serializers.ValidationError('This gathering is not open for reservations.')
        remaining = gathering.capacity - gathering.attendee_count
        if quantity > remaining:
            raise serializers.ValidationError(f'Only {max(remaining, 0)} places remain for this gathering.')

        tier = None
        tiers = TicketTier.objects.select_for_update().filter(gathering=gathering)
        if tier_id.isdigit():
            tier = tiers.filter(id=int(tier_id)).first()
        if tier is None and requested_tier_name:
            tier = tiers.filter(name=requested_tier_name).first()
        if tier is None and tiers.exists():
            raise serializers.ValidationError({'tierId': 'Please choose a valid ticket tier.'})

        if tier is not None:
            if quantity > tier.available:
                raise serializers.ValidationError(f'Only {tier.available} "{tier.name}" tickets remain.')
            unit_price = tier.price
            tier_name = tier.name
            tier.available -= quantity
            tier.save(update_fields=['available'])
        else:
            unit_price = gathering.starting_price
            tier_name = 'General Admission'

        base_total = unit_price * quantity
        discount = (base_total * PROMO_CODES[promo]).quantize(Decimal('1'), ROUND_HALF_UP) if promo else Decimal('0')

        booking = Booking.objects.create(
            user=self.context['request'].user,
            gathering=gathering,
            tier_name=tier_name,
            unit_price=unit_price,
            discount=discount,
            total_price=max(Decimal('0'), base_total - discount),
            currency=gathering.currency,
            ticket_code=generate_ticket_code(),
            **validated_data,
        )

        gathering.attendee_count += quantity
        if gathering.attendee_count >= gathering.capacity:
            gathering.status = 'sold_out'
        gathering.save(update_fields=['attendee_count', 'status'])
        return booking
