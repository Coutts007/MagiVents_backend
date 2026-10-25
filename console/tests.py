from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from gatherings.models import Gathering
from payments.models import Payment
from tickets.models import Booking
from .models import ConsoleAction

SECRET = 'ops-7Yq2-xK9m-Lp4w-Zr8t'
BASE = f'/api/console/{SECRET}'


@override_settings(ADMIN_CONSOLE_PATH=SECRET, ADMIN_CONSOLE_MAX_FAILURES=5, PAYMENTS_SIMULATED_DELAY_SECONDS=0)
class ConsoleTestCase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        call_command('seed_gatherings', verbosity=0)
        self.staff = User.objects.create_user(username='ops@x.com', email='ops@x.com', password='pw-Staff-12345',
                                              is_staff=True)
        self.organizer = User.objects.get(email='curator@magivents.local')
        self.event = Gathering.objects.get(id='nairobi-sounds-live')

    def book(self, quantity=2, **extra):
        tier = self.event.tiers.first()
        self.client.force_authenticate(None)
        res = self.client.post('/api/bookings/', {
            'eventId': self.event.id, 'tierId': str(tier.id), 'quantity': quantity, 'attendeeName': 'Guest',
            'attendeeEmail': 'guest@x.com', 'paymentMethod': 'card', **extra}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data


class AccessTests(ConsoleTestCase):
    def test_only_staff_with_the_secret_path_get_in(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f'{BASE}/session/').status_code, 200)

    def test_everyone_else_gets_a_plain_404(self):
        attendee = User.objects.create_user(username='a@x.com', email='a@x.com', password='pw-Attendee-123')
        cases = [
            (None, f'{BASE}/overview/'),                      # signed out
            (attendee, f'{BASE}/overview/'),                  # not staff
            (self.organizer, f'{BASE}/payments/'),            # organizer, not staff
            (self.staff, '/api/console/wrong-path-123456/overview/'),  # staff, wrong path
        ]
        for user, url in cases:
            self.client.force_authenticate(user)
            res = self.client.get(url)
            self.assertEqual(res.status_code, 404, (user, url))
            # The same answer as any other missing page, whoever asks
            self.assertEqual(res.data, {'error': 'We could not find what you were looking for. It may have been removed.',
                                        'code': 'not_found'})

    def test_inactive_staff_are_refused(self):
        self.staff.is_active = False
        self.staff.save()
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f'{BASE}/session/').status_code, 404)

    @override_settings(ADMIN_CONSOLE_PATH='')
    def test_disabled_when_not_configured(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/console//session/').status_code, 404)
        self.assertEqual(self.client.get(f'{BASE}/session/').status_code, 404)

    @override_settings(ADMIN_CONSOLE_PATH='short')
    def test_short_secrets_disable_the_console(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/console/short/session/').status_code, 404)

    def test_guessing_locks_the_caller_out(self):
        self.client.force_authenticate(self.staff)
        for i in range(5):
            self.client.get(f'/api/console/guess-number-{i:04d}/session/')
        # Even the right path is refused for an hour now
        self.assertEqual(self.client.get(f'{BASE}/session/').status_code, 404)

    def test_not_in_the_public_api_schema(self):
        schema = self.client.get('/api/schema/').content.decode()
        self.assertNotIn('/api/console/', schema)


class OverviewAndListsTests(ConsoleTestCase):
    def test_overview_counts_confirmed_sales_only(self):
        paid = self.book()
        with override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=60):
            self.book(quantity=1)  # stays pending
        self.client.force_authenticate(self.staff)
        data = self.client.get(f'{BASE}/overview/').data
        self.assertEqual(data['sales']['allTime'], {'revenue': paid['totalPrice'], 'tickets': 2, 'bookings': 1})
        self.assertEqual(data['sales']['today']['bookings'], 1)
        self.assertEqual(data['pendingHolds'], {'bookings': 1, 'tickets': 1})
        self.assertEqual(data['bookingsByStatus'], {'confirmed': 1, 'pending': 1})
        self.assertEqual(len(data['daily']), 30)
        self.assertEqual(data['daily'][-1]['revenue'], paid['totalPrice'])
        self.assertEqual(data['byMethod'], [{'method': 'card', 'revenue': paid['totalPrice'], 'bookings': 1}])
        self.assertEqual(data['topOrganizers'][0]['id'], str(self.organizer.id))
        self.assertEqual(data['users']['organizers'], 1)

    def test_organizer_list_and_detail(self):
        paid = self.book()
        self.client.force_authenticate(self.staff)
        listed = self.client.get(f'{BASE}/organizers/').data
        self.assertEqual(listed['count'], 1)
        row = listed['results'][0]
        self.assertEqual((row['email'], row['eventsCount'], row['ticketsSold'], row['revenue']),
                         ('curator@magivents.local', 10, 2, paid['totalPrice']))
        detail = self.client.get(f"{BASE}/organizers/{row['id']}/").data
        self.assertEqual(len(detail['events']), 10)
        sounds = next(e for e in detail['events'] if e['id'] == self.event.id)
        self.assertEqual((sounds['ticketsSold'], sounds['revenue']), (2, paid['totalPrice']))
        self.assertEqual(self.client.get(f'{BASE}/organizers/', {'search': 'nobody-here'}).data['count'], 0)

    def test_bookings_and_payments_search_and_filters(self):
        paid = self.book()
        self.client.force_authenticate(self.staff)
        bookings = self.client.get(f'{BASE}/bookings/', {'search': paid['ticketCode']}).data
        self.assertEqual(bookings['results'][0]['id'], paid['id'])
        self.assertTrue(bookings['results'][0]['isGuest'])
        self.assertEqual(self.client.get(f'{BASE}/bookings/', {'status': 'expired'}).data['count'], 0)
        payments = self.client.get(f'{BASE}/payments/', {'status': 'succeeded', 'search': 'guest@x.com'}).data
        self.assertEqual(payments['count'], 1)
        payment = payments['results'][0]
        self.assertEqual(payment['booking']['ticketCode'], paid['ticketCode'])
        detail = self.client.get(f"{BASE}/payments/{payment['id']}/").data
        self.assertIn('rawPayloads', detail)


class ActionTests(ConsoleTestCase):
    def test_refund_queue_is_resolved_with_a_note_and_logged(self):
        paid = self.book()
        payment = Payment.objects.get(booking_id=paid['id'])
        payment.needs_refund = True
        payment.save()
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f'{BASE}/payments/', {'refund': 'open'}).data['count'], 1)
        self.assertEqual(self.client.get(f'{BASE}/overview/').data['refundsOpen'],
                         {'count': 1, 'amount': float(payment.amount)})

        url = f'{BASE}/payments/{payment.id}/resolve-refund/'
        self.assertEqual(self.client.post(url, {'note': ''}, format='json').status_code, 400)
        res = self.client.post(url, {'note': 'Refunded on Paystack dashboard'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['refundResolvedBy'], 'ops@x.com')
        self.assertEqual(self.client.post(url, {'note': 'again'}, format='json').status_code, 400)
        self.assertEqual(self.client.get(f'{BASE}/payments/', {'refund': 'open'}).data['count'], 0)
        self.assertEqual(self.client.get(f'{BASE}/payments/', {'refund': 'resolved'}).data['count'], 1)
        log = self.client.get(f'{BASE}/actions/').data['results'][0]
        self.assertEqual((log['action'], log['staff'], log['note']),
                         ('resolve_refund', 'ops@x.com', 'Refunded on Paystack dashboard'))

    def test_revoke_and_restore_organizer_role(self):
        self.client.force_authenticate(self.staff)
        url = f'{BASE}/organizers/{self.organizer.id}/role/'
        res = self.client.post(url, {'role': 'patron', 'note': 'Unverified organizer'}, format='json')
        self.assertEqual(res.data['role'], 'patron')
        # They can no longer create events, but still appear in the organizer list (they own events)
        self.client.force_authenticate(User.objects.get(id=self.organizer.id))
        self.assertEqual(self.client.post('/api/gatherings/', {}, format='json').status_code, 403)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f'{BASE}/organizers/').data['count'], 1)
        self.client.post(url, {'role': 'curator'}, format='json')
        self.assertEqual(User.objects.get(id=self.organizer.id).role, 'curator')
        self.assertEqual(ConsoleAction.objects.filter(target_type='user').count(), 2)

    def test_unpublish_and_republish_an_event(self):
        self.client.force_authenticate(self.staff)
        url = f'{BASE}/events/{self.event.id}/status/'
        self.assertEqual(self.client.post(url, {'status': 'draft', 'note': 'Reported'}, format='json').data['status'], 'draft')
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/gatherings/{self.event.id}/').status_code, 404)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(url, {'status': 'published'}, format='json').data['status'], 'published')
        # A full event goes back as sold out
        self.event.refresh_from_db()
        self.event.attendee_count = self.event.capacity
        self.event.save()
        self.client.post(url, {'status': 'draft'}, format='json')
        self.assertEqual(self.client.post(url, {'status': 'published'}, format='json').data['status'], 'sold_out')
