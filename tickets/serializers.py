from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from gatherings.models import Gathering, TicketTier
from .documents import ticket_url
from .models import Booking, PromoCode


def promo_discount(amount, percent_off):
    """Discount in whole shillings, rounded half up."""
    return (Decimal(amount) * Decimal(percent_off) / Decimal(100)).quantize(Decimal('1'), ROUND_HALF_UP)




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
            # People the booking admits in total, and per ticket (a couple ticket admits 2)
            'admits': obj.people,
            'admitsPerTicket': obj.admits_per_ticket,
            'unitPrice': float(obj.unit_price),
            'isSalePrice': obj.is_sale_price,
            'totalPrice': float(obj.total_price),
            'attendeeName': obj.attendee_name,
            'attendeeEmail': obj.attendee_email,
            'bookingDate': obj.booking_date.strftime('%b %d, %Y').replace(' 0', ' '),
            # Issued only once the booking is confirmed
            'ticketCode': obj.ticket_code if obj.is_confirmed else None,
            'status': obj.status,
            'expiresAt': obj.expires_at.isoformat() if obj.status == 'pending' and obj.expires_at else None,
            'paymentMethod': obj.payment_method,
            'mpesaPhoneNumber': obj.mpesa_phone_number or None,
            'mpesaReceiptNumber': obj.mpesa_receipt_number or None,
            'mpesaMode': obj.mpesa_mode or None,
            'currency': obj.currency,
            'totalInKes': float(obj.total_in_kes) if obj.total_in_kes is not None else None,
            'notes': obj.notes or None,
            # The QR code encodes this link, which carries the booking's UUID
            'ticketUrl': ticket_url(obj) if obj.is_confirmed else None,
            'checkedInAt': obj.checked_in_at.isoformat() if obj.checked_in_at else None,
        }

    def validate_promoCode(self, value):
        return value.strip().upper()

    @transaction.atomic
    def create(self, validated_data):
        from payments.services import confirm_free_booking, hold_duration

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

        if gathering.is_free:
            # One registration per person: checked under the gathering lock, so two quick submits cannot both pass
            if quantity != 1:
                raise serializers.ValidationError({'quantity': 'Free events allow one ticket per person.'})
            user = self.context['request'].user
            person = Q(attendee_email__iexact=validated_data['attendee_email'])
            if user.is_authenticated:
                person |= Q(user=user)
            if gathering.bookings.filter(person, status='confirmed').exists():
                raise serializers.ValidationError('You are already registered for this event.')

        tier = None
        tiers = TicketTier.objects.select_for_update().filter(gathering=gathering)
        if tier_id.isdigit():
            tier = tiers.filter(id=int(tier_id)).first()
        if tier is None and requested_tier_name:
            tier = tiers.filter(name=requested_tier_name).first()
        if tier is None and tiers.exists():
            raise serializers.ValidationError({'tierId': 'Please choose a valid ticket tier.'})

        # Everything below runs under the gathering and tier row locks taken above, so two buyers can never
        # both get the last place, the last ticket, or the last ticket at the flash-sale price
        now = timezone.now()
        admits, is_sale = (tier.admits if tier is not None else 1), False
        people = quantity * admits
        remaining = gathering.capacity - gathering.attendee_count
        if people > remaining:
            raise serializers.ValidationError(f'Only {max(remaining, 0)} places remain for this gathering.')

        if tier is not None:
            state = tier.sales_state(now)
            if state == 'not_started':
                raise serializers.ValidationError(f'"{tier.name}" tickets are not on sale yet.')
            if state == 'ended':
                raise serializers.ValidationError(f'Sales of "{tier.name}" tickets have closed.')
            if quantity > tier.available:
                raise serializers.ValidationError(f'Only {tier.available} "{tier.name}" tickets remain.')
            unit_price, is_sale = tier.price_at(now)
            if is_sale and tier.sale_remaining is not None and quantity > tier.sale_remaining:
                # Never charge a mix of prices, and never charge more than the price the buyer was shown
                raise serializers.ValidationError(
                    f'Only {tier.sale_remaining} "{tier.name}" tickets are left at the sale price. '
                    f'Choose {tier.sale_remaining} or fewer.'
                )
            tier_name = tier.name
            tier.available -= quantity
            if is_sale:
                tier.sale_sold += quantity
            tier.save(update_fields=['available', 'sale_sold'])
        else:
            unit_price = gathering.starting_price
            tier_name = 'General Admission'

        base_total = unit_price * quantity
        discount = Decimal('0')
        code = None
        if promo:
            code = PromoCode.lookup(promo, gathering)
            if code is None:
                raise serializers.ValidationError({'promoCode': 'Invalid promotion code.'})
            code = PromoCode.objects.select_for_update().get(pk=code.pk)
            problem = code.problem(gathering)
            if problem:
                raise serializers.ValidationError({'promoCode': problem})
            discount = promo_discount(base_total, code.percent_off)
            code.use_count += 1
            code.save(update_fields=['use_count'])

        total = max(Decimal('0'), base_total - discount)
        # Payment details come from the provider, never from the client
        validated_data.pop('mpesa_receipt_number', None)
        validated_data.pop('total_in_kes', None)
        if total == 0:
            # Free entry: nothing to pay, so payment details are not kept
            validated_data.update(payment_method='free', mpesa_phone_number='', mpesa_mode='')
        else:
            validated_data['total_in_kes'] = total
            validated_data.setdefault('payment_method', 'mpesa')
            if validated_data['payment_method'] not in ('mpesa', 'card'):
                raise serializers.ValidationError({'paymentMethod': 'Pay with M-Pesa or card.'})

        user = self.context['request'].user
        booking = Booking.objects.create(
            # Guests book without an account; their ticket is tied to the attendee email only
            user=user if user.is_authenticated else None,
            gathering=gathering,
            tier=tier,
            tier_name=tier_name,
            admits_per_ticket=admits,
            unit_price=unit_price,
            is_sale_price=is_sale,
            discount=discount,
            total_price=total,
            currency=gathering.currency,
            promo_code=code,
            # Paid bookings hold their tickets until the payment confirms or the hold expires
            status='pending' if total > 0 else 'confirmed',
            expires_at=timezone.now() + hold_duration() if total > 0 else None,
            **validated_data,
        )

        gathering.attendee_count += people
        if gathering.attendee_count >= gathering.capacity:
            gathering.status = 'sold_out'
        gathering.save(update_fields=['attendee_count', 'status'])
        if total == 0:
            # Ticket code and confirmation email (sent after commit; a failed email never fails the booking)
            confirm_free_booking(booking)
        return booking


class PromoCodeSerializer(serializers.ModelSerializer):
    """Codes an organizer manages for one of their own events."""
    id = serializers.CharField(read_only=True)
    eventId = serializers.CharField(write_only=True, required=False)
    eventTitle = serializers.CharField(source='gathering.title', read_only=True)
    percentOff = serializers.IntegerField(source='percent_off', min_value=1, max_value=100)
    validFrom = serializers.DateTimeField(source='valid_from', required=False, allow_null=True)
    validUntil = serializers.DateTimeField(source='valid_until', required=False, allow_null=True)
    maxUses = serializers.IntegerField(source='max_uses', required=False, allow_null=True, min_value=1)
    useCount = serializers.IntegerField(source='use_count', read_only=True)
    isActive = serializers.BooleanField(source='is_active', required=False)

    class Meta:
        model = PromoCode
        fields = ['id', 'code', 'percentOff', 'eventId', 'eventTitle', 'validFrom', 'validUntil',
                  'maxUses', 'useCount', 'isActive']

    def to_representation(self, obj):
        data = super().to_representation(obj)
        data['eventId'] = str(obj.gathering_id) if obj.gathering_id else None
        return data

    def validate_code(self, value):
        value = value.strip().upper()
        if not value.replace('-', '').isalnum():
            raise serializers.ValidationError('Use letters, numbers and dashes only.')
        return value

    def validate_eventId(self, value):
        user = self.context['request'].user
        gathering = Gathering.objects.filter(id=value, organizer=user).first()
        if gathering is None:
            raise serializers.ValidationError('Choose one of your own events.')
        return gathering

    def validate(self, attrs):
        gathering = attrs.pop('eventId', None) or getattr(self.instance, 'gathering', None)
        if gathering is None:
            # Site-wide codes are managed by staff in the Django admin
            raise serializers.ValidationError({'eventId': 'Choose one of your own events.'})
        attrs['gathering'] = gathering
        start = attrs.get('valid_from', getattr(self.instance, 'valid_from', None))
        end = attrs.get('valid_until', getattr(self.instance, 'valid_until', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'validUntil': 'The end must be after the start.'})
        code = attrs.get('code', getattr(self.instance, 'code', ''))
        clash = PromoCode.objects.filter(code=code, gathering=gathering).exclude(pk=getattr(self.instance, 'pk', None))
        if clash.exists():
            raise serializers.ValidationError({'code': 'This event already has a code with that name.'})
        return attrs


class PromoValidateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)
    eventId = serializers.CharField()
    tierId = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.IntegerField(required=False, min_value=1, max_value=20)


def normalize_ticket_code(value):
    """Accepts "mv-123456", "MV 123456" or just "123456"."""
    digits = ''.join(ch for ch in value if ch.isdigit())
    return f'MV-{digits}' if digits else value.strip().upper()


class TicketLookupSerializer(serializers.Serializer):
    ticketCode = serializers.CharField(max_length=60)
    email = serializers.EmailField()


class CheckInSerializer(serializers.Serializer):
    ticket = serializers.CharField(max_length=500, help_text='Ticket code, booking UUID, or the URL in the QR code')


def attendee_row(booking):
    return {
        'id': str(booking.id),
        'ticketCode': booking.ticket_code,
        'attendeeName': booking.attendee_name,
        'attendeeEmail': booking.attendee_email,
        'tierName': booking.tier_name,
        'quantity': booking.quantity,
        'admits': booking.people,
        'bookingDate': booking.booking_date.isoformat(),
        'checkedInAt': booking.checked_in_at.isoformat() if booking.checked_in_at else None,
    }
