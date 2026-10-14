import re

from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from console.models import ConsoleAction
from gatherings.models import Gathering
from gatherings.tests import event_payload, give_payout_details
from listings.models import CategoryFee, ListingFee, PlatformPaymentAccount
from notifications.models import Notification
from payouts.models import PayoutAccount
from tickets.models import Booking

MUSIC = 'Entertainment & Music'

ADMIN_ENDPOINTS = [
    ('get', '/api/admin/overview/'),
    ('get', '/api/admin/organizers/'),
    ('post', '/api/admin/organizers/'),
    ('get', '/api/admin/organizers/{org}/'),
    ('patch', '/api/admin/organizers/{org}/'),
    ('delete', '/api/admin/organizers/{org}/'),
    ('post', '/api/admin/organizers/{org}/resend-invite/'),
    ('put', '/api/admin/organizers/{org}/payment-details/'),
    ('delete', '/api/admin/organizers/{org}/payment-details/'),
    ('post', '/api/admin/organizers/{org}/suspend/'),
    ('post', '/api/admin/organizers/{org}/reactivate/'),
    ('get', '/api/admin/organizers/{org}/payouts/'),
    ('post', '/api/admin/organizers/{org}/payouts/'),
    ('get', '/api/admin/category-fees/'),
    ('put', '/api/admin/category-fees/'),
    ('get', '/api/admin/platform-payment/'),
    ('put', '/api/admin/platform-payment/'),
    ('get', '/api/admin/listing-fees/'),
    ('post', '/api/admin/listing-fees/{event}/waive/'),
    ('post', '/api/admin/listing-fees/{event}/mark-paid/'),
]


class AdminTestCase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin@x.com', email='admin@x.com', password='pw-Adm-12345',
                                              role='admin')
        self.organizer = User.objects.create_user(username='org@x.com', email='org@x.com', password='pw-Org-12345',
                                                  role='curator', first_name='Wanjiku')
        give_payout_details(self.organizer)
        CategoryFee.objects.create(category=MUSIC, amount=500)
        self.client.force_authenticate(self.organizer)
        self.event_id = self.client.post('/api/gatherings/', event_payload(category=MUSIC), format='json').data['id']
        self.client.force_authenticate(self.admin)


class AdminAccessTests(AdminTestCase):
    def test_every_admin_endpoint_refuses_non_admins(self):
        attendee = User.objects.create_user(username='p@x.com', email='p@x.com', password='pw-Pat-12345')
        # A staff member's role follows is_staff; a demoted former admin is refused too
        former = User.objects.create_user(username='f@x.com', email='f@x.com', password='pw-For-12345', role='admin')
        former.role = 'curator'
        former.save()
        self.assertFalse(User.objects.get(pk=former.pk).is_staff)
        inactive = User.objects.create_user(username='i@x.com', email='i@x.com', password='pw-Ina-12345',
                                            role='admin', is_active=False)
        for user in (attendee, self.organizer, former, inactive):
            self.client.force_authenticate(user)
            for method, url in ADMIN_ENDPOINTS:
                url = url.format(org=self.organizer.id, event=self.event_id)
                res = getattr(self.client, method)(url, {}, format='json')
                self.assertEqual(res.status_code, 403, f'{user.email} {method.upper()} {url} -> {res.status_code}')
        self.client.force_authenticate(None)
        for method, url in ADMIN_ENDPOINTS:
            url = url.format(org=self.organizer.id, event=self.event_id)
            self.assertEqual(getattr(self.client, method)(url, {}, format='json').status_code, 401)
        # Nothing changed
        self.assertFalse(User.objects.get(pk=self.organizer.pk).is_suspended)
        self.assertEqual(Gathering.objects.get(id=self.event_id).status, 'draft')

    def test_admins_get_in(self):
        self.assertEqual(self.client.get('/api/admin/overview/').status_code, 200)
        superuser = User.objects.create_superuser(username='s@x.com', email='s@x.com', password='pw-Sup-12345')
        self.client.force_authenticate(superuser)
        self.assertEqual(self.client.get('/api/admin/organizers/').status_code, 200)


class OrganizerManagementTests(AdminTestCase):
    # Django's reset pages use the admin's CSS; tests run without collectstatic
    @override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                                 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
    def test_create_organizer_sends_a_set_password_link_that_works(self):
        res = self.client.post('/api/admin/organizers/', {'name': 'Otieno Events', 'email': 'Otieno@Example.com',
                                                          'city': 'Kisumu'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['role'], res.data['hasPassword'], res.data['inviteSent']), ('curator', False, True))
        user = User.objects.get(email='otieno@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'http://testserver(/accounts/reset/\S+/)', mail.outbox[0].body).group(1)

        # Django's reset-confirm page: open the link, set the password, land back in the app
        self.client.force_authenticate(None)
        page = self.client.get(link, follow=True)
        self.assertEqual(page.status_code, 200)
        set_url = page.redirect_chain[-1][0]
        done = self.client.post(set_url, {'new_password1': 'Sturdy-pass-42', 'new_password2': 'Sturdy-pass-42'})
        self.assertEqual(done.status_code, 302)
        self.assertTrue(self.client.get(done['Location']).url.endswith('/?signin=1'))
        login = self.client.post('/api/auth/login/', {'email': 'otieno@example.com', 'password': 'Sturdy-pass-42'},
                                 format='json')
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.data['user']['role'], 'curator')

        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post('/api/admin/organizers/', {'name': 'Dup', 'email': 'otieno@example.com'},
                                          format='json').status_code, 400)
        self.assertTrue(ConsoleAction.objects.filter(action='create_organizer', staff=self.admin).exists())

    def test_list_search_and_filters(self):
        User.objects.create_user(username='z@x.com', email='z@x.com', password='pw-Zed-12345', role='curator',
                                 is_suspended=True, first_name='Zawadi')
        rows = self.client.get('/api/admin/organizers/').data['results']
        self.assertEqual({r['email'] for r in rows}, {'org@x.com', 'z@x.com'})
        org = next(r for r in rows if r['email'] == 'org@x.com')
        self.assertEqual((org['eventsCount'], org['paymentDetails']['summary']), (1, 'M-Pesa 0712 345 678'))
        self.assertEqual([r['email'] for r in self.client.get('/api/admin/organizers/', {'status': 'suspended'}).data['results']],
                         ['z@x.com'])
        self.assertEqual([r['email'] for r in self.client.get('/api/admin/organizers/', {'search': 'wanj'}).data['results']],
                         ['org@x.com'])

    def test_edit_details_role_and_payment_details(self):
        url = f'/api/admin/organizers/{self.organizer.id}/'
        res = self.client.patch(url, {'name': 'Wanjiku M.', 'email': 'new@x.com', 'city': 'Nakuru', 'bio': 'Hi'},
                                format='json')
        self.assertEqual((res.data['name'], res.data['email'], res.data['city']), ('Wanjiku M.', 'new@x.com', 'Nakuru'))
        self.assertEqual(self.client.patch(url, {'email': 'admin@x.com'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {'role': 'patron'}, format='json').data['role'], 'patron')
        self.assertEqual(self.client.patch(url, {'role': 'curator'}, format='json').data['role'], 'curator')
        res = self.client.put(f'{url}payment-details/', {'method': 'till', 'tillNumber': '5123456'}, format='json')
        self.assertEqual(res.data['details']['summary'], 'Till 5123456')
        self.assertEqual(PayoutAccount.objects.get(organizer=self.organizer, gathering=None).updated_by, self.admin)
        detail = self.client.get(url).data
        self.assertEqual(detail['paymentDetails']['tillNumber'], '5123456')
        self.assertEqual(detail['events'][0]['feeStatus'], 'unpaid')
        admin_url = f'/api/admin/organizers/{self.admin.id}/'
        self.assertEqual(self.client.patch(admin_url, {'role': 'patron'}, format='json').status_code, 400)

    def test_suspend_takes_events_down_and_reactivate(self):
        Gathering.objects.filter(id=self.event_id).update(status='published')
        url = f'/api/admin/organizers/{self.organizer.id}/'
        self.assertEqual(self.client.post(f'{url}suspend/', {'takeDownEvents': True}, format='json').status_code, 400)
        res = self.client.post(f'{url}suspend/', {'reason': 'Fake event reported', 'takeDownEvents': True}, format='json')
        self.assertEqual((res.data['isSuspended'], res.data['eventsTakenDown']), (True, 1))
        self.assertEqual(Gathering.objects.get(id=self.event_id).status, 'draft')

        self.organizer.refresh_from_db()
        self.client.force_authenticate(self.organizer)
        self.assertEqual(self.client.post('/api/gatherings/', event_payload(), format='json').status_code, 403)
        self.assertEqual(self.client.get('/api/auth/profile/').data['suspensionReason'], 'Fake event reported')

        self.client.force_authenticate(self.admin)
        self.assertFalse(self.client.post(f'{url}reactivate/').data['isSuspended'])

    def test_suspend_can_leave_events_up(self):
        Gathering.objects.filter(id=self.event_id).update(status='published')
        self.client.post(f'/api/admin/organizers/{self.organizer.id}/suspend/', {'reason': 'Check'}, format='json')
        self.assertEqual(Gathering.objects.get(id=self.event_id).status, 'published')

    def test_delete_needs_the_email_and_no_bookings(self):
        url = f'/api/admin/organizers/{self.organizer.id}/'
        self.assertEqual(self.client.delete(url, {'confirmEmail': 'wrong@x.com'}, format='json').status_code, 400)
        Gathering.objects.filter(id=self.event_id).update(status='published')
        Booking.objects.create(gathering_id=self.event_id, tier_name='VIP', unit_price=10, total_price=10,
                               attendee_name='A', attendee_email='a@x.com', status='confirmed')
        res = self.client.delete(url, {'confirmEmail': 'ORG@x.com'}, format='json')
        self.assertEqual((res.status_code, res.data['code'], res.data['canSuspend']), (409, 'has_bookings', True))
        self.assertTrue(User.objects.filter(pk=self.organizer.pk).exists())

        Booking.objects.all().delete()
        self.assertEqual(self.client.delete(url, {'confirmEmail': 'org@x.com'}, format='json').status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.organizer.pk).exists())
        self.assertFalse(Gathering.objects.filter(id=self.event_id).exists())


class FeesAndPlatformTests(AdminTestCase):
    def test_category_fees_table(self):
        fees = self.client.get('/api/admin/category-fees/').data['fees']
        self.assertEqual(len(fees), 9)
        music = next(f for f in fees if f['category'] == MUSIC)
        self.assertEqual((music['amount'], music['eventsAwaitingPayment']), (500, 1))
        self.assertEqual(next(f for f in fees if f['category'] == 'Others')['amount'], 0)

        # Exempting the category clears the unpaid event; a negative amount is refused
        self.assertEqual(self.client.put('/api/admin/category-fees/', {'fees': [{'category': MUSIC, 'amount': -1}]},
                                         format='json').status_code, 400)
        res = self.client.put('/api/admin/category-fees/', {'fees': [{'category': MUSIC, 'amount': 0},
                                                                     {'category': 'Others', 'amount': 250}]}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(ListingFee.objects.get(gathering_id=self.event_id).status, 'exempt')
        self.assertEqual(CategoryFee.objects.get(category='Others').updated_by, self.admin)

    def test_platform_payment_details(self):
        self.assertEqual(self.client.put('/api/admin/platform-payment/', {'method': 'paybill', 'businessNumber': '12',
                                                                          'accountName': 'X'}, format='json').status_code, 400)
        res = self.client.put('/api/admin/platform-payment/', {
            'method': 'till', 'businessNumber': '5123456', 'accountNumber': 'IGNORED', 'accountName': 'MagiVents Ltd',
            'instructions': 'Lipa na M-Pesa, Buy Goods.'}, format='json')
        self.assertEqual((res.data['method'], res.data['accountNumber']), ('till', ''))
        self.assertEqual(PlatformPaymentAccount.load().updated_by, self.admin)
        # Organizers see it in their Pay to publish step
        self.client.force_authenticate(self.organizer)
        state = self.client.get(f'/api/gatherings/{self.event_id}/listing-fee/').data
        self.assertEqual((state['platform']['businessNumber'], state['platform']['accountName']), ('5123456', 'MagiVents Ltd'))


class ListingFeeAdminTests(AdminTestCase):
    def test_list_filters_and_totals(self):
        res = self.client.get('/api/admin/listing-fees/', {'status': 'unpaid'})
        self.assertEqual(res.data['count'], 1)
        row = res.data['results'][0]
        self.assertEqual((row['eventId'], row['amountDue'], row['organizer']['email']), (self.event_id, 500, 'org@x.com'))
        self.assertEqual(res.data['totals']['amountUnpaid'], 500)
        self.assertEqual(self.client.get('/api/admin/listing-fees/', {'status': 'paid'}).data['count'], 0)
        self.assertEqual(self.client.get('/api/admin/listing-fees/', {'search': 'org@x'}).data['count'], 1)

    def test_waive_needs_a_reason_and_is_recorded(self):
        url = f'/api/admin/listing-fees/{self.event_id}/waive/'
        self.assertEqual(self.client.post(url, {'reason': ''}, format='json').status_code, 400)
        res = self.client.post(url, {'reason': 'Charity event'}, format='json')
        self.assertEqual((res.data['status'], res.data['waivedBy'], res.data['waiveReason'], res.data['amountDue']),
                         ('waived', 'admin@x.com', 'Charity event', 0))
        self.assertIsNotNone(res.data['waivedAt'])
        self.assertEqual(self.client.post(url, {'reason': 'Again'}, format='json').status_code, 400)
        self.client.force_authenticate(self.organizer)
        self.assertEqual(self.client.patch(f'/api/gatherings/{self.event_id}/', {'status': 'published'},
                                           format='json').status_code, 200)

    @override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=600)
    def test_mark_paid_records_the_money_and_publishes_when_asked(self):
        other_admin = User.objects.create_user(username='a2@x.com', email='a2@x.com', password='pw-Adm-23456', role='admin')
        # The organizer asked to publish and started a payment that never completed
        self.client.force_authenticate(self.organizer)
        self.client.post(f'/api/gatherings/{self.event_id}/listing-fee/pay/', {'mpesaPhoneNumber': '0712345678'},
                         format='json')
        self.client.force_authenticate(self.admin)
        url = f'/api/admin/listing-fees/{self.event_id}/mark-paid/'
        self.assertEqual(self.client.post(url, {'reason': 'Paid at office'}, format='json').status_code, 400)
        res = self.client.post(url, {'reason': 'Paid to the Paybill', 'reference': 'QX12ABC'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['status'], res.data['amountPaid'], res.data['paymentReference'], res.data['markedPaidBy']),
                         ('paid', 500, 'QX12ABC', 'admin@x.com'))
        self.assertEqual(Gathering.objects.get(id=self.event_id).status, 'published')
        self.assertTrue(Notification.objects.filter(recipient=other_admin, kind='listing_fee_paid').exists())
        self.assertFalse(Notification.objects.filter(recipient=self.admin, kind='listing_fee_paid').exists())
        overview = self.client.get('/api/admin/overview/').data
        self.assertEqual((overview['feesCollectedThisMonth'], overview['eventsAwaitingPayment']), (500.0, 0))
