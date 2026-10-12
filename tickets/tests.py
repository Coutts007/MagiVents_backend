from django.test import override_settings
import math

from django.core.management import call_command
from rest_framework.test import APITestCase

from accounts.models import User
from gatherings.models import Gathering
from tickets.models import Booking


# Paid bookings confirm within the request when the simulated gateway has no delay (Phase 4)
@override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0)
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


# Paid bookings confirm within the request when the simulated gateway has no delay (Phase 4)
@override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0)
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


from unittest import mock

from django.core import mail
from django.test import override_settings


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', FRONTEND_URL='https://magivents.test',
                   PAYMENTS_SIMULATED_DELAY_SECONDS=0)
class TicketDeliveryTests(APITestCase):
    def setUp(self):
        call_command('seed_gatherings', verbosity=0)
        self.event = Gathering.objects.get(id='nairobi-sounds-live')
        self.tier = self.event.tiers.first()
        self.organizer = User.objects.get(email='curator@magivents.local')

    def book(self, email='guest@x.com', event=None, tier=None):
        event, tier = event or self.event, tier or self.tier
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/bookings/', {
                'eventId': event.id, 'tierId': str(tier.id), 'quantity': 2, 'attendeeName': 'Wanjiru Guest',
                'attendeeEmail': email, 'paymentMethod': 'card'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data

    def test_confirmation_email_has_html_text_and_pdf(self):
        booking = self.book()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['guest@x.com'])
        self.assertIn(booking['ticketCode'], message.subject)
        self.assertIn(booking['ticketCode'], message.body)
        html, mime = message.alternatives[0]
        self.assertEqual(mime, 'text/html')
        self.assertIn(f"https://magivents.test/?ticket={booking['id']}", html)
        name, content, mimetype = message.attachments[0]
        self.assertEqual(mimetype, 'application/pdf')
        self.assertTrue(content.startswith(b'%PDF'))

    def test_failed_email_never_fails_the_booking(self):
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('SMTP down')):
            booking = self.book()
        self.assertTrue(Booking.objects.filter(id=booking['id']).exists())

    def test_qr_url_uses_uuid_and_pdf_download(self):
        booking = self.book()
        self.assertEqual(booking['ticketUrl'], f"https://magivents.test/?ticket={booking['id']}")
        self.assertNotIn(booking['ticketCode'], booking['ticketUrl'])
        res = self.client.get(f"/api/tickets/{booking['id']}/pdf/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertTrue(res.content.startswith(b'%PDF'))
        self.assertEqual(self.client.get(f"/api/tickets/{booking['id']}/").data['ticketCode'], booking['ticketCode'])

    def test_resend_goes_to_the_attendee(self):
        booking = self.book()
        mail.outbox.clear()
        res = self.client.post(f"/api/tickets/{booking['id']}/resend/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(mail.outbox[0].to, ['guest@x.com'])
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('SMTP down')):
            self.assertEqual(self.client.post(f"/api/tickets/{booking['id']}/resend/").status_code, 503)

    def test_guest_lookup_needs_code_and_email(self):
        booking = self.book()
        url = '/api/tickets/lookup/'
        ok = self.client.post(url, {'ticketCode': booking['ticketCode'].lower(), 'email': 'GUEST@x.com'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data['id'], booking['id'])
        wrong_email = self.client.post(url, {'ticketCode': booking['ticketCode'], 'email': 'x@x.com'}, format='json')
        wrong_code = self.client.post(url, {'ticketCode': 'MV-000000', 'email': 'guest@x.com'}, format='json')
        self.assertEqual(wrong_email.status_code, 404)
        self.assertEqual(wrong_email.data, wrong_code.data)

    def test_lookup_is_throttled(self):
        from django.core.cache import cache
        from rest_framework.throttling import ScopedRateThrottle
        cache.clear()
        self.addCleanup(cache.clear)
        # Throttle rates are read once at import, so patch the class attribute
        patcher = mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {'ticket_lookup': '2/hour'})
        patcher.start()
        self.addCleanup(patcher.stop)
        url = '/api/tickets/lookup/'
        for _ in range(2):
            self.client.post(url, {'ticketCode': 'MV-1', 'email': 'a@x.com'}, format='json')
        self.assertEqual(self.client.post(url, {'ticketCode': 'MV-1', 'email': 'a@x.com'}, format='json').status_code, 429)

    def test_check_in_by_organizer_only(self):
        booking = self.book()
        url = f'/api/gatherings/{self.event.id}/check-in/'

        stranger = User.objects.create_user(username='s@x.com', email='s@x.com', password='pw-Stranger-1', role='curator')
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.post(url, {'ticket': booking['ticketCode']}, format='json').status_code, 403)

        self.client.force_authenticate(self.organizer)
        # Scanning the QR code sends the whole URL
        res = self.client.post(url, {'ticket': booking['ticketUrl']}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['result'], 'checked_in')
        again = self.client.post(url, {'ticket': booking['ticketCode'].lower()}, format='json')
        self.assertEqual(again.data['result'], 'already_checked_in')
        self.assertEqual(again.data['attendee']['checkedInAt'], res.data['attendee']['checkedInAt'])

        other_event = Gathering.objects.get(id='sme-growth-summit')
        self.client.force_authenticate(None)
        other = self.book(event=other_event, tier=other_event.tiers.first())
        self.client.force_authenticate(self.organizer)
        wrong = self.client.post(url, {'ticket': other['id']}, format='json')
        self.assertEqual(wrong.data['result'], 'wrong_event')
        self.assertNotIn('attendee', wrong.data)
        self.assertEqual(self.client.post(url, {'ticket': 'MV-999999'}, format='json').data['result'], 'not_found')

        attendees = self.client.get(f'/api/gatherings/{self.event.id}/attendees/').data
        self.assertEqual(attendees['counts'], {'bookings': 1, 'people': 2, 'checkedInBookings': 1, 'checkedInPeople': 2})
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.get(f'/api/gatherings/{self.event.id}/attendees/').status_code, 403)
