import math

from django.core.management import call_command
from rest_framework.test import APITestCase

from accounts.models import User
from gatherings.models import Gathering
from tickets.models import Booking


class BookingAndBookmarkTests(APITestCase):
    def setUp(self):
        call_command('seed_gatherings', verbosity=0)
        self.user = User.objects.create_user(username='u@x.com', email='u@x.com', password='pw-User-123')
        self.client.force_authenticate(self.user)
        self.event = Gathering.objects.get(id='nairobi-sounds-live')
        self.tier = self.event.tiers.first()

    def book(self, **overrides):
        payload = {
            'eventId': self.event.id, 'tierId': str(self.tier.id), 'tierName': self.tier.name, 'quantity': 2,
            'attendeeName': 'U Ser', 'attendeeEmail': 'u@x.com', 'paymentMethod': 'mpesa',
            'mpesaPhoneNumber': '0712345678', 'mpesaReceiptNumber': 'SFK1234AB', 'mpesaMode': 'stk',
            'totalInKes': 24510,
        }
        payload.update(overrides)
        return self.client.post('/api/bookings/', payload, format='json')

    def test_booking_prices_server_side_and_updates_counts(self):
        attendees, available = self.event.attendee_count, self.tier.available
        res = self.book(promoCode='karibu15')
        self.assertEqual(res.status_code, 201, res.data)
        expected_base = float(self.tier.price) * 2
        self.assertEqual(res.data['unitPrice'], float(self.tier.price))
        self.assertEqual(res.data['totalPrice'], expected_base - math.floor(expected_base * 0.15 + 0.5))
        self.assertTrue(res.data['ticketCode'].startswith('MV-'))
        self.assertEqual(res.data['eventTitle'], self.event.title)

        self.event.refresh_from_db(); self.tier.refresh_from_db()
        self.assertEqual(self.event.attendee_count, attendees + 2)
        self.assertEqual(self.tier.available, available - 2)

        listed = self.client.get('/api/bookings/').data
        self.assertEqual([b['id'] for b in listed], [res.data['id']])

    def test_tier_by_name_when_id_unknown(self):
        res = self.book(tierId='tier-from-old-client')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['tierName'], self.tier.name)

    def test_rejects_bad_promo_and_overbooking(self):
        self.assertEqual(self.book(promoCode='FREE').status_code, 400)
        self.assertEqual(self.book(quantity=self.tier.available + 1).status_code, 400)

    def test_sells_out(self):
        self.event.attendee_count = self.event.capacity - 1
        self.event.save()
        self.assertEqual(self.book(quantity=1).status_code, 201)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'sold_out')
        self.assertEqual(self.book(quantity=1).status_code, 400)

    def test_bookings_are_private(self):
        self.book()
        other = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Other-123')
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get('/api/bookings/').data, [])

    def test_bookmark_toggle_and_list(self):
        url = f'/api/bookmarks/toggle/{self.event.id}/'
        self.assertEqual(self.client.post(url).status_code, 201)
        self.assertEqual(self.client.get('/api/bookmarks/').data, [self.event.id])
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.get('/api/bookmarks/').data, [])

    def test_free_event_booking_needs_no_payment(self):
        free_event = Gathering.objects.get(id='nairobi-dev-meetup-payments')
        free_tier = free_event.tiers.first()
        res = self.book(eventId=free_event.id, tierId=str(free_tier.id), tierName=free_tier.name, quantity=1)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['totalPrice'], 0)
        self.assertEqual(res.data['paymentMethod'], 'free')
        self.assertIsNone(res.data['mpesaReceiptNumber'])
        self.assertEqual(res.data['currency'], 'KES')

    def test_guest_can_book_without_account(self):
        self.client.force_authenticate(None)
        res = self.book(attendeeEmail='guest@x.com')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['ticketCode'].startswith('MV-'))
        self.assertIsNone(Booking.objects.get(id=res.data['id']).user)
        # Guests still cannot list bookings
        self.assertEqual(self.client.get('/api/bookings/').status_code, 401)


from datetime import timedelta

from django.utils import timezone

from tickets.models import PromoCode


class PromoCodeTests(APITestCase):
    def setUp(self):
        call_command('seed_gatherings', verbosity=0)
        self.organizer = User.objects.get(email='curator@magivents.local')
        self.event = Gathering.objects.get(id='nairobi-sounds-live')
        self.tier = self.event.tiers.first()

    def book(self, code, quantity=1):
        return self.client.post('/api/bookings/', {
            'eventId': self.event.id, 'tierId': str(self.tier.id), 'quantity': quantity,
            'attendeeName': 'G', 'attendeeEmail': 'g@x.com', 'paymentMethod': 'card', 'promoCode': code,
        }, format='json')

    def test_karibu15_is_seeded_site_wide(self):
        code = PromoCode.objects.get(code='KARIBU15')
        self.assertIsNone(code.gathering)
        self.assertEqual(code.percent_off, 15)

    def test_validate_endpoint_previews_discount_without_using_it(self):
        res = self.client.post('/api/promo-codes/validate/', {
            'code': 'karibu15', 'eventId': self.event.id, 'tierId': str(self.tier.id), 'quantity': 2,
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        subtotal = float(self.tier.price) * 2
        self.assertEqual(res.data['percentOff'], 15)
        self.assertEqual(res.data['subtotal'], subtotal)
        self.assertEqual(res.data['total'], subtotal - res.data['discount'])
        self.assertEqual(PromoCode.objects.get(code='KARIBU15').use_count, 0)

        bad = self.client.post('/api/promo-codes/validate/', {'code': 'NOPE', 'eventId': self.event.id}, format='json')
        self.assertEqual(bad.status_code, 400)

    def test_event_code_limits_expiry_and_scope(self):
        other_event = Gathering.objects.get(id='sme-growth-summit')
        PromoCode.objects.create(code='SOUNDS50', percent_off=50, gathering=self.event, max_uses=1)
        PromoCode.objects.create(code='OLD', percent_off=10, valid_until=timezone.now() - timedelta(days=1))

        res = self.book('sounds50')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['totalPrice'], float(self.tier.price) / 2)
        self.assertEqual(PromoCode.objects.get(code='SOUNDS50').use_count, 1)
        # Used up
        self.assertEqual(self.book('SOUNDS50').status_code, 400)
        # Expired
        self.assertEqual(self.book('OLD').status_code, 400)
        # Only valid for its own event
        res = self.client.post('/api/promo-codes/validate/', {'code': 'SOUNDS50', 'eventId': other_event.id},
                               format='json')
        self.assertEqual(res.status_code, 400)

    def test_failed_booking_does_not_use_the_code(self):
        PromoCode.objects.create(code='ONCE', percent_off=20, gathering=self.event, max_uses=1)
        self.assertEqual(self.book('ONCE', quantity=self.tier.available + 1).status_code, 400)
        self.assertEqual(PromoCode.objects.get(code='ONCE').use_count, 0)

    def test_organizers_manage_codes_for_their_own_events_only(self):
        self.client.force_authenticate(self.organizer)
        res = self.client.post('/api/promo-codes/', {'code': 'early-bird', 'percentOff': 20, 'eventId': self.event.id},
                               format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['code'], 'EARLY-BIRD')
        self.assertEqual(res.data['eventTitle'], self.event.title)
        code_id = res.data['id']
        self.assertEqual(len(self.client.get('/api/promo-codes/').data), 1)
        res = self.client.patch(f'/api/promo-codes/{code_id}/', {'isActive': False}, format='json')
        self.assertFalse(res.data['isActive'])

        stranger = User.objects.create_user(username='s@x.com', email='s@x.com', password='pw-Stranger-1', role='curator')
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.get('/api/promo-codes/').data, [])
        self.assertEqual(self.client.delete(f'/api/promo-codes/{code_id}/').status_code, 404)
        res = self.client.post('/api/promo-codes/', {'code': 'X', 'percentOff': 20, 'eventId': self.event.id},
                               format='json')
        self.assertEqual(res.status_code, 400)
        # Site-wide codes are not created through the API
        res = self.client.post('/api/promo-codes/', {'code': 'X', 'percentOff': 20}, format='json')
        self.assertEqual(res.status_code, 400)
