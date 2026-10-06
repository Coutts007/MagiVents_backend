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
