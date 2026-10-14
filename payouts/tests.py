import json

from rest_framework.test import APITestCase

from accounts.models import User
from gatherings.models import Gathering
from gatherings.tests import event_payload
from .models import PayoutAccount

PHONE = {'method': 'mpesa_phone', 'phoneNumber': '0712 345 678'}


def give_payout_details(user, **fields):
    """Test helper: the organizer's default payment details (paid events need them to publish)."""
    return PayoutAccount.objects.update_or_create(
        organizer=user, gathering=None, defaults={'method': 'mpesa_phone', 'phone_number': '254712345678', **fields}
    )[0]


class PayoutDetailsValidationTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Org-12345',
                                                  role='curator')
        self.client.force_authenticate(self.organizer)

    def put(self, data):
        return self.client.put('/api/payouts/details/', data, format='json')

    def test_each_method_is_validated(self):
        self.assertEqual(self.put({'method': 'mpesa_phone', 'phoneNumber': '12345'}).status_code, 400)
        res = self.put(PHONE)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['details']['phoneNumber'], '0712345678')
        self.assertEqual(PayoutAccount.objects.get().phone_number, '254712345678')

        self.assertEqual(self.put({'method': 'till', 'tillNumber': '12ab'}).status_code, 400)
        self.assertEqual(self.put({'method': 'till', 'tillNumber': '5123456'}).data['details']['summary'], 'Till 5123456')

        self.assertEqual(self.put({'method': 'paybill', 'paybillNumber': '247247'}).status_code, 400)
        self.assertEqual(self.put({'method': 'paybill', 'paybillNumber': '12', 'paybillAccount': 'A1'}).status_code, 400)
        res = self.put({'method': 'paybill', 'paybillNumber': '247247', 'paybillAccount': 'acc-001'})
        self.assertEqual((res.data['details']['paybillNumber'], res.data['details']['paybillAccount']), ('247247', 'ACC-001'))

        self.assertEqual(self.put({'method': 'bank', 'bankName': 'Equity', 'bankAccountName': 'A',
                                   'bankAccountNumber': '0123456789'}).status_code, 400)
        self.assertEqual(self.put({'method': 'bank', 'bankName': 'Equity', 'bankAccountName': 'Wanjiku Events',
                                   'bankAccountNumber': '12-34'}).status_code, 400)
        res = self.put({'method': 'bank', 'bankName': 'Equity Bank', 'bankAccountName': 'Wanjiku Events',
                        'bankAccountNumber': '0123 4567 8901'})
        self.assertEqual(res.status_code, 200, res.data)
        # Replacing the method clears the previous one's fields; there is still one default
        account = PayoutAccount.objects.get()
        self.assertEqual((account.method, account.paybill_number, account.bank_account_number), ('bank', '', '012345678901'))

        self.assertEqual(self.client.delete('/api/payouts/details/').status_code, 204)
        self.assertIsNone(self.client.get('/api/payouts/details/').data['details'])

    def test_attendees_have_no_payment_details(self):
        self.client.force_authenticate(User.objects.create_user(username='p@x.com', email='p@x.com',
                                                                password='pw-Pat-12345'))
        self.assertEqual(self.put(PHONE).status_code, 403)
        self.assertEqual(self.client.get('/api/payouts/details/').status_code, 403)


class EventPayoutDetailsTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Org-12345',
                                                  role='curator')
        self.other = User.objects.create_user(username='x@x.com', email='x@x.com', password='pw-Oth-12345',
                                              role='curator')
        self.client.force_authenticate(self.organizer)
        self.event_id = self.client.post('/api/gatherings/', event_payload(status='draft'), format='json').data['id']
        self.url = f'/api/payouts/events/{self.event_id}/details/'

    def publish(self):
        return self.client.patch(f'/api/gatherings/{self.event_id}/', {'status': 'published'}, format='json')

    def test_paid_event_needs_payment_details_to_publish(self):
        res = self.publish()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'payment_details_missing')
        event = self.client.get(f'/api/gatherings/{self.event_id}/').data
        event['status'] = 'published'
        self.assertEqual(self.client.put(f'/api/gatherings/{self.event_id}/', event, format='json').status_code, 400)
        self.assertEqual(Gathering.objects.get(id=self.event_id).status, 'draft')

        # An override for just this event is enough
        res = self.client.put(self.url, {'method': 'till', 'tillNumber': '5123456'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['usesDefault'], res.data['effective']['summary']), (False, 'Till 5123456'))
        self.assertEqual(self.publish().status_code, 200)

    def test_default_details_apply_and_override_wins(self):
        give_payout_details(self.organizer)
        self.assertEqual(self.client.get(self.url).data['effective']['summary'], 'M-Pesa 0712 345 678')
        self.client.put(self.url, {'method': 'till', 'tillNumber': '5123456'}, format='json')
        self.assertEqual(self.client.get(self.url).data['effective']['summary'], 'Till 5123456')
        res = self.client.delete(self.url)
        self.assertEqual((res.data['usesDefault'], res.data['effective']['summary']), (True, 'M-Pesa 0712 345 678'))

    def test_free_event_needs_no_payment_details(self):
        free = self.client.post('/api/gatherings/', event_payload(isFree=True, status='draft'), format='json').data
        res = self.client.patch(f"/api/gatherings/{free['id']}/", {'status': 'published'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)

    def test_only_the_owner_can_read_or_change_event_details(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.put(self.url, PHONE, format='json').status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)


class PaymentDetailsArePrivateTests(APITestCase):
    def test_never_in_a_public_response(self):
        organizer = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Org-12345',
                                             role='curator')
        give_payout_details(organizer, method='bank', phone_number='', bank_name='Secret Bank',
                            bank_account_name='Hidden Name', bank_account_number='99887766554433')
        self.client.force_authenticate(organizer)
        event_id = self.client.post('/api/gatherings/', event_payload(status='draft'), format='json').data['id']
        PayoutAccount.objects.create(organizer=organizer, gathering_id=event_id, method='till', till_number='7654321')
        self.assertEqual(self.client.patch(f'/api/gatherings/{event_id}/', {'status': 'published'},
                                           format='json').status_code, 200)
        secrets = ['Secret Bank', 'Hidden Name', '99887766554433', '7654321', 'payout']

        def assert_clean(response):
            body = json.dumps(response.data, default=str)
            for secret in secrets:
                self.assertNotIn(secret, body)

        for user in (None, User.objects.create_user(username='v@x.com', email='v@x.com', password='pw-Vis-12345'),
                     organizer):
            self.client.force_authenticate(user)
            assert_clean(self.client.get('/api/gatherings/'))
            assert_clean(self.client.get(f'/api/gatherings/{event_id}/'))
            assert_clean(self.client.get('/api/gatherings/', {'search': 'Secret'}))
