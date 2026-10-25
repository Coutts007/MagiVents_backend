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


# --- Ticket types: couple and group tickets, flash sales, sales windows -------------------------------
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.core.cache import cache

from gatherings.models import TicketTier
from gatherings.tests import event_payload
from tickets.models import PromoCode

T0 = datetime(2030, 3, 1, 9, 0, tzinfo=dt_timezone.utc)


@override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0)
class TierTypeBookingTests(APITestCase):
    def setUp(self):
        # Guest bookings are rate limited per IP in the (shared) cache
        cache.clear()
        self.addCleanup(cache.clear)
        self.organizer = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Org-12345',
                                                  role='curator')
        self.client.force_authenticate(self.organizer)
        payload = event_payload(isFree=False, status='draft')
        payload['pricing'] = {'currency': 'KES', 'startingPrice': 1, 'tiers': [
            {'name': 'Regular', 'price': 1000, 'quantity': 20},
            {'name': 'Couple', 'price': 1800, 'quantity': 10, 'admits': 2},
            {'name': 'Group of 5', 'price': 4000, 'quantity': 2, 'admits': 5},
            {'name': 'VIP', 'price': 3000, 'quantity': 10, 'salePrice': 2000,
             'saleStartsAt': T0.isoformat(), 'saleEndsAt': (T0 + timedelta(hours=2)).isoformat(), 'saleQuantity': 3},
            {'name': 'Early Bird', 'price': 800, 'quantity': 10,
             'onSaleFrom': T0.isoformat(), 'onSaleUntil': (T0 + timedelta(days=1)).isoformat()},
        ]}
        res = self.client.post('/api/gatherings/', payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.event_id = res.data['id']
        # Publishing rules are tested elsewhere; here the event just needs to be on sale
        Gathering.objects.filter(id=self.event_id).update(status='published')
        self.tiers = {t.name: t for t in TicketTier.objects.filter(gathering_id=self.event_id)}
        self.client.force_authenticate(None)

    def book(self, tier, quantity=1, at=T0 + timedelta(minutes=30), **extra):
        payload = {'eventId': self.event_id, 'tierId': str(self.tiers[tier].id), 'quantity': quantity,
                   'attendeeName': 'Guest', 'attendeeEmail': 'guest@x.com', 'paymentMethod': 'mpesa',
                   'mpesaPhoneNumber': '0712345678', 'totalInKes': 1, **extra}
        with mock.patch('django.utils.timezone.now', return_value=at):
            return self.client.post('/api/bookings/', payload, format='json')

    def event(self):
        return Gathering.objects.get(id=self.event_id)

    def test_capacity_counts_people_not_tickets(self):
        # 20×1 + 10×2 + 2×5 + 10×1 + 10×1
        self.assertEqual(self.event().capacity, 70)

    def test_couple_ticket_admits_two_at_one_price(self):
        res = self.book('Couple')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['unitPrice'], res.data['totalPrice']), (1800.0, 1800.0))
        self.assertEqual((res.data['quantity'], res.data['admits'], res.data['admitsPerTicket']), (1, 2, 2))
        self.assertEqual(res.data['status'], 'confirmed')
        self.assertEqual(self.event().attendee_count, 2)
        self.tiers['Couple'].refresh_from_db()
        self.assertEqual(self.tiers['Couple'].available, 9)

        # The booking keeps what it was sold as, even if the tier changes later
        TicketTier.objects.filter(pk=self.tiers['Couple'].pk).update(price=5000)
        ticket = self.client.get(f"/api/tickets/{res.data['id']}/").data
        self.assertEqual((ticket['unitPrice'], ticket['admits']), (1800.0, 2))

    def test_group_tickets_and_the_last_one(self):
        res = self.book('Group of 5', quantity=2)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['totalPrice'], res.data['admits']), (8000.0, 10))
        self.assertEqual(self.event().attendee_count, 10)
        res = self.book('Group of 5')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.event().attendee_count, 10)

    def test_last_place_is_not_oversold_by_a_group_ticket(self):
        # Only 3 places left in the event: a group ticket for 5 must be refused even though the tier has stock
        Gathering.objects.filter(id=self.event_id).update(attendee_count=67)
        res = self.book('Group of 5')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Only 3 places remain', str(res.data))
        self.assertEqual(self.book('Couple').status_code, 201)
        self.assertEqual(self.event().attendee_count, 69)

    def test_flash_sale_price_and_its_boundaries(self):
        start, end = T0, T0 + timedelta(hours=2)
        before = self.book('VIP', at=start - timedelta(seconds=1))
        at_start = self.book('VIP', at=start)
        at_end = self.book('VIP', at=end)
        self.assertEqual(before.data['unitPrice'], 3000.0)
        self.assertEqual((at_start.data['unitPrice'], at_start.data['isSalePrice']), (2000.0, True))
        self.assertEqual((at_end.data['unitPrice'], at_end.data['isSalePrice']), (3000.0, False))
        self.tiers['VIP'].refresh_from_db()
        self.assertEqual(self.tiers['VIP'].sale_sold, 1)

    def test_flash_sale_limit_and_the_last_sale_ticket(self):
        self.assertEqual(self.book('VIP', quantity=2).data['totalPrice'], 4000.0)
        # One left at the sale price: asking for two is refused rather than charging a mix of prices
        res = self.book('VIP', quantity=2)
        self.assertEqual(res.status_code, 400)
        self.assertIn('Only 1', str(res.data))
        last = self.book('VIP')
        self.assertEqual((last.data['unitPrice'], last.data['isSalePrice']), (2000.0, True))
        # The allowance is used up: the normal price is back, still during the sale window
        after = self.book('VIP')
        self.assertEqual((after.data['unitPrice'], after.data['isSalePrice']), (3000.0, False))
        public = self.client.get(f'/api/gatherings/{self.event_id}/').data
        vip = next(t for t in public['pricing']['tiers'] if t['name'] == 'VIP')
        self.assertEqual((vip['saleRemaining'], vip['saleQuantity']), (0, 3))

    @override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=600)
    def test_failed_payment_returns_the_sale_ticket_and_the_places(self):
        res = self.book('VIP', quantity=3, mpesaPhoneNumber='0712341032')
        self.assertEqual(res.data['status'], 'pending')
        self.tiers['VIP'].refresh_from_db()
        self.assertEqual(self.tiers['VIP'].sale_sold, 3)
        from payments.models import Payment
        from payments.services import record_failure
        record_failure(Payment.objects.get(booking_id=res.data['id']).id, 'Cancelled')
        self.tiers['VIP'].refresh_from_db()
        self.assertEqual((self.tiers['VIP'].sale_sold, self.tiers['VIP'].available), (0, 10))
        self.assertEqual(self.event().attendee_count, 0)

    def test_promo_code_applies_on_top_of_the_sale_price(self):
        PromoCode.objects.create(code='HALF', percent_off=50, gathering_id=self.event_id)
        res = self.book('VIP', promoCode='half')
        self.assertEqual((res.data['unitPrice'], res.data['totalPrice']), (2000.0, 1000.0))
        with mock.patch('django.utils.timezone.now', return_value=T0 + timedelta(minutes=5)):
            check = self.client.post('/api/promo-codes/validate/', {
                'code': 'HALF', 'eventId': self.event_id, 'tierId': str(self.tiers['VIP'].id), 'quantity': 1},
                format='json')
        self.assertEqual((check.data['subtotal'], check.data['total']), (2000.0, 1000.0))

    def test_sales_window_opens_and_closes_the_tier(self):
        self.assertEqual(self.book('Early Bird', at=T0 - timedelta(minutes=1)).status_code, 400)
        self.assertEqual(self.book('Early Bird', at=T0).status_code, 201)
        closed = self.book('Early Bird', at=T0 + timedelta(days=1))
        self.assertEqual(closed.status_code, 400)
        self.assertIn('closed', str(closed.data))

    def test_public_tier_payload_shows_the_price_in_effect(self):
        with mock.patch('django.utils.timezone.now', return_value=T0 + timedelta(minutes=1)):
            tiers = self.client.get(f'/api/gatherings/{self.event_id}/').data['pricing']['tiers']
        vip = next(t for t in tiers if t['name'] == 'VIP')
        self.assertEqual((vip['price'], vip['salePrice'], vip['currentPrice'], vip['saleActive']),
                         (3000.0, 2000.0, 2000.0, True))
        self.assertEqual(vip['saleEndsAt'], (T0 + timedelta(hours=2)).isoformat())
        couple = next(t for t in tiers if t['name'] == 'Couple')
        self.assertEqual((couple['admits'], couple['saleActive'], couple['salesState']), (2, False, 'on_sale'))


class TierValidationTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username='v@x.com', email='v@x.com', password='pw-Val-12345',
                                                  role='curator')
        self.client.force_authenticate(self.organizer)

    def create(self, **tier):
        payload = event_payload(isFree=False, status='draft')
        payload['pricing'] = {'currency': 'KES', 'startingPrice': 1, 'tiers': [{'name': 'VIP', 'price': 3000,
                                                                               'quantity': 10, **tier}]}
        return self.client.post('/api/gatherings/', payload, format='json')

    def test_sale_rules(self):
        times = {'saleStartsAt': T0.isoformat(), 'saleEndsAt': (T0 + timedelta(hours=1)).isoformat()}
        self.assertEqual(self.create(salePrice=3000, **times).status_code, 400)       # not lower than the price
        self.assertEqual(self.create(salePrice=1000).status_code, 400)                 # no times
        self.assertEqual(self.create(salePrice=1000, saleStartsAt=times['saleEndsAt'],
                                     saleEndsAt=times['saleStartsAt']).status_code, 400)  # ends before it starts
        self.assertEqual(self.create(salePrice=1000, saleQuantity=11, **times).status_code, 400)  # over quantity
        self.assertEqual(self.create(salePrice=1000, saleQuantity=5, **times).status_code, 201)
        self.assertEqual(self.create(admits=0).status_code, 400)

    def test_free_event_clears_prices_and_sales(self):
        payload = event_payload(isFree=True)
        payload['pricing'] = {'currency': 'KES', 'startingPrice': 0, 'tiers': [{'name': 'Couple', 'price': 0,
                                                                               'quantity': 5, 'admits': 2}]}
        res = self.client.post('/api/gatherings/', payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        tier = res.data['pricing']['tiers'][0]
        self.assertEqual((tier['price'], tier['salePrice'], tier['admits']), (0.0, None, 2))
        self.assertEqual(res.data['capacity'], 10)

    @override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0)
    def test_admits_cannot_change_once_sold(self):
        res = self.create(admits=2)
        gid = res.data['id']
        Gathering.objects.filter(id=gid).update(status='published')
        tier_id = res.data['pricing']['tiers'][0]['id']
        self.client.post('/api/bookings/', {'eventId': gid, 'tierId': tier_id, 'quantity': 1, 'attendeeName': 'A',
                                            'attendeeEmail': 'a@x.com', 'paymentMethod': 'card'}, format='json')
        event = self.client.get(f'/api/gatherings/{gid}/').data
        event['pricing']['tiers'][0]['admits'] = 3
        self.assertEqual(self.client.put(f'/api/gatherings/{gid}/', event, format='json').status_code, 400)
        event['pricing']['tiers'][0]['admits'] = 2
        self.assertEqual(self.client.put(f'/api/gatherings/{gid}/', event, format='json').status_code, 200)


@override_settings(BOOKING_EMAILS_IN_BACKGROUND=True, PAYMENTS_SIMULATED_DELAY_SECONDS=0)
class BackgroundEmailTests(APITestCase):
    """With BOOKING_EMAILS_IN_BACKGROUND the booking request only marks the email; the worker sends it."""

    def setUp(self):
        call_command('seed_gatherings', verbosity=0)
        self.event = Gathering.objects.get(id='nairobi-sounds-live')
        self.tier = self.event.tiers.first()

    def test_booking_marks_the_email_and_the_worker_sends_it_once(self):
        from tickets.management.commands.send_booking_emails import send_due_emails
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/bookings/', {
                'eventId': self.event.id, 'tierId': str(self.tier.id), 'quantity': 1, 'attendeeName': 'Achieng',
                'attendeeEmail': 'achieng@x.com', 'paymentMethod': 'card'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['status'], 'confirmed')
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(Booking.objects.get(id=res.data['id']).confirmation_email_due)

        self.assertEqual(send_due_emails(), 1)
        self.assertEqual(mail.outbox[0].to, ['achieng@x.com'])
        self.assertFalse(Booking.objects.get(id=res.data['id']).confirmation_email_due)
        self.assertEqual(send_due_emails(), 0)

    def test_unreachable_mail_server_keeps_the_email_for_the_next_run(self):
        from tickets.management.commands.send_booking_emails import send_due_emails
        booking = Booking.objects.create(gathering=self.event, quantity=1, unit_price=0, total_price=0,
                                         attendee_name='Otieno', attendee_email='otieno@x.com', status='confirmed',
                                         ticket_code='MV-123456', confirmation_email_due=True)
        with mock.patch('django.core.mail.backends.locmem.EmailBackend.open', side_effect=OSError('SMTP down')):
            self.assertEqual(send_due_emails(), 0)
        self.assertTrue(Booking.objects.get(id=booking.id).confirmation_email_due)
        self.assertEqual(send_due_emails(), 1)
        self.assertEqual(mail.outbox[0].to, ['otieno@x.com'])


class FreeEventLimitTests(APITestCase):
    """Free events: one ticket per booking and one registration per person."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        call_command('seed_gatherings', verbosity=0)
        self.event = Gathering.objects.get(id='nairobi-sounds-live')
        self.event.is_free = True
        self.event.save(update_fields=['is_free'])
        self.event.tiers.update(price=0, sale_price=None)
        self.tier = self.event.tiers.first()

    def register(self, email='guest@x.com', quantity=1):
        return self.client.post('/api/bookings/', {
            'eventId': self.event.id, 'tierId': str(self.tier.id), 'quantity': quantity,
            'attendeeName': 'Guest', 'attendeeEmail': email}, format='json')

    def test_only_one_ticket_per_registration(self):
        res = self.register(quantity=2)
        self.assertEqual(res.status_code, 400)
        self.assertIn('quantity', res.data)
        self.assertEqual(self.register().status_code, 201)

    def test_guest_email_registers_once(self):
        self.assertEqual(self.register('guest@x.com').status_code, 201)
        again = self.register('GUEST@x.com')
        self.assertEqual(again.status_code, 400)
        self.assertIn('already registered', str(again.data))
        self.assertEqual(self.register('other@x.com').status_code, 201)
        self.assertEqual(self.event.bookings.filter(status='confirmed').count(), 2)

    def test_guest_gets_the_ticket_email_at_the_address_they_gave(self):
        # No account and not signed in: the email goes to the attendee email from the form
        with self.captureOnCommitCallbacks(execute=True):
            res = self.register('wanjiru.guest@x.com')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(Booking.objects.get(id=res.data['id']).user)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['wanjiru.guest@x.com'])
        self.assertIn(self.event.title, message.subject)
        self.assertIn('confirmed', message.body)
        self.assertEqual(message.attachments[0][0], f"MagiVents-Ticket-{res.data['ticketCode']}.pdf")

    def test_signed_in_user_registers_once_whatever_the_email(self):
        user = User.objects.create_user(username='u@x.com', email='u@x.com', password='pw-User-123')
        self.client.force_authenticate(user)
        self.assertEqual(self.register('u@x.com').status_code, 201)
        self.assertEqual(self.register('another@x.com').status_code, 400)

    def test_paid_events_still_allow_several_tickets(self):
        self.event.is_free = False
        self.event.save(update_fields=['is_free'])
        self.event.tiers.update(price=500)
        res = self.client.post('/api/bookings/', {
            'eventId': self.event.id, 'tierId': str(self.tier.id), 'quantity': 3, 'attendeeName': 'Guest',
            'attendeeEmail': 'guest@x.com', 'paymentMethod': 'card'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
