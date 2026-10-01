import math

from django.core.management import call_command
from rest_framework.test import APITestCase

from accounts.models import User
from gatherings.models import Gathering


class BookingAndBookmarkTests(APITestCase):
    def setUp(self):
        call_command('seed_gatherings', verbosity=0)
        self.user = User.objects.create_user(username='u@x.com', email='u@x.com', password='pw-User-123')
        self.client.force_authenticate(self.user)
        self.event = Gathering.objects.get(id='symphony-in-the-quarry')
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
        res = self.book(promoCode='magisand')
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
