import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from gatherings.models import Gathering
from tickets.models import Booking
from .models import Payment
from .phone import normalize_kenyan_phone
from .services import record_failure, record_success

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


class PhoneNumberTests(SimpleTestCase):
    def test_normalizes_kenyan_mobile_numbers(self):
        for raw in ['0712 345 678', '+254712345678', '254712345678', '712345678', '0712-345-678']:
            self.assertEqual(normalize_kenyan_phone(raw), '254712345678')
        self.assertEqual(normalize_kenyan_phone('0110 123 456'), '254110123456')
        for bad in ['', '12345', '0812345678', 'abc', '+1 555 123 4567']:
            with self.assertRaises(ValueError):
                normalize_kenyan_phone(bad)


class LifecycleBase(APITestCase):
    def setUp(self):
        cache.clear()
        call_command('seed_gatherings', verbosity=0)
        self.event = Gathering.objects.get(id='nairobi-sounds-live')
        self.tier = self.event.tiers.first()

    def book(self, quantity=2, method='mpesa', mode='stk', phone='0712345678', **extra):
        payload = {'eventId': self.event.id, 'tierId': str(self.tier.id), 'quantity': quantity,
                   'attendeeName': 'Njeri Buyer', 'attendeeEmail': 'njeri@example.com',
                   'paymentMethod': method, 'mpesaMode': mode, 'mpesaPhoneNumber': phone, **extra}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post('/api/bookings/', payload, format='json')

    def status(self, booking_id):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.get(f'/api/bookings/{booking_id}/status/')

    def counts(self):
        self.event.refresh_from_db()
        self.tier.refresh_from_db()
        return self.event.attendee_count, self.tier.available


@override_settings(EMAIL_BACKEND=LOCMEM, PAYMENTS_MODE='simulated', PAYMENTS_SIMULATED_DELAY_SECONDS=60)
class SimulatedLifecycleTests(LifecycleBase):
    def test_paid_booking_is_pending_then_confirmed(self):
        before = self.counts()
        res = self.book()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['status'], 'pending')
        self.assertIsNone(res.data['ticketCode'])
        self.assertIsNone(res.data['ticketUrl'])
        self.assertEqual(res.data['payment']['status'], 'pending')
        self.assertGreater(res.data['holdSecondsLeft'], settings.PAYMENT_HOLD_MINUTES * 60 - 10)
        # Tickets are held while the payment is pending
        self.assertEqual(self.counts(), (before[0] + 2, before[1] - 2))
        booking_id = res.data['id']

        # No ticket, PDF or email until it is paid
        self.assertEqual(self.client.get(f'/api/tickets/{booking_id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/tickets/{booking_id}/pdf/').status_code, 404)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.status(booking_id).data['status'], 'pending')

        with override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0):
            done = self.status(booking_id)
        self.assertEqual(done.data['status'], 'confirmed')
        self.assertTrue(done.data['ticketCode'].startswith('MV-'))
        self.assertTrue(done.data['mpesaReceiptNumber'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.counts(), (before[0] + 2, before[1] - 2))

    def test_failure_releases_tickets_and_retry_reuses_the_booking(self):
        before = self.counts()
        with override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0):
            res = self.book(phone='0712341032')  # the simulated gateway "cancels" numbers ending 1032
        self.assertEqual(res.data['status'], 'failed')
        self.assertIn('cancelled', res.data['payment']['failureReason'])
        self.assertEqual(self.counts(), before)

        with override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0), self.captureOnCommitCallbacks(execute=True):
            retry = self.client.post(f"/api/bookings/{res.data['id']}/pay/",
                                     {'paymentMethod': 'mpesa', 'mpesaMode': 'stk', 'mpesaPhoneNumber': '0712345678'},
                                     format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data['id'], res.data['id'])
        self.assertEqual(retry.data['status'], 'confirmed')
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(self.counts(), (before[0] + 2, before[1] - 2))
        # Paying again is refused
        again = self.client.post(f"/api/bookings/{res.data['id']}/pay/", {'paymentMethod': 'card'}, format='json')
        self.assertEqual(again.status_code, 409)

    def test_expired_holds_are_released_and_sold_out_reopens(self):
        self.event.capacity = self.event.attendee_count + 2
        self.event.save()
        before = self.counts()
        res = self.book(quantity=2)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'sold_out')

        Booking.objects.filter(id=res.data['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        # Lazily released when availability is read
        self.client.get('/api/gatherings/')
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'expired')
        self.assertEqual(self.counts(), before)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'published')
        # Running the command again changes nothing
        call_command('release_expired_holds', verbosity=0)
        self.assertEqual(self.counts(), before)

    def test_command_releases_expired_holds(self):
        before = self.counts()
        res = self.book()
        Booking.objects.filter(id=res.data['id']).update(expires_at=timezone.now() - timedelta(minutes=1))
        call_command('release_expired_holds', verbosity=0)
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'expired')
        self.assertEqual(self.counts(), before)

    def test_pending_bookings_are_not_tickets_sales_or_attendees(self):
        user = User.objects.create_user(username='n@x.com', email='n@x.com', password='pw-Buyer-1234')
        self.client.force_authenticate(user)
        self.book()
        self.assertEqual(self.client.get('/api/bookings/').data, [])
        organizer = User.objects.get(email='curator@magivents.local')
        self.client.force_authenticate(organizer)
        self.assertEqual(self.client.get(f'/api/gatherings/{self.event.id}/').data['organizerStats'],
                         {'revenue': 0.0, 'ticketsSold': 0})
        self.assertEqual(self.client.get(f'/api/gatherings/{self.event.id}/attendees/').data['counts']['bookings'], 0)

    def test_promo_use_is_given_back_when_the_hold_is_released(self):
        from tickets.models import PromoCode
        code = PromoCode.objects.get(code='KARIBU15')
        res = self.book(promoCode='KARIBU15')
        code.refresh_from_db()
        self.assertEqual(code.use_count, 1)
        Booking.objects.filter(id=res.data['id']).update(expires_at=timezone.now() - timedelta(minutes=1))
        call_command('release_expired_holds', verbosity=0)
        code.refresh_from_db()
        self.assertEqual(code.use_count, 0)


@override_settings(EMAIL_BACKEND=LOCMEM, PAYMENTS_MODE='simulated', PAYMENTS_SIMULATED_DELAY_SECONDS=60)
class IdempotencyTests(LifecycleBase):
    def pending(self, quantity=2):
        res = self.book(quantity=quantity)
        return Booking.objects.get(id=res.data['id']), Payment.objects.get(booking_id=res.data['id'])

    def test_repeated_success_confirms_once(self):
        booking, payment = self.pending()
        for _ in range(3):
            with self.captureOnCommitCallbacks(execute=True):
                record_success(payment.id, booking.total_price, receipt='ABC123XYZ')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')
        self.assertEqual(len(mail.outbox), 1)
        code = booking.ticket_code
        record_failure(payment.id, 'late failure')
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.ticket_code), ('confirmed', code))

    def test_repeated_failure_releases_once(self):
        before = self.counts()
        booking, payment = self.pending()
        record_failure(payment.id, 'cancelled')
        record_failure(payment.id, 'cancelled')
        self.assertEqual(self.counts(), before)

    def test_amount_mismatch_never_confirms(self):
        booking, payment = self.pending()
        record_success(payment.id, booking.total_price - 1)
        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, 'pending')
        self.assertEqual(payment.status, 'failed')
        self.assertTrue(payment.needs_refund)

    def test_late_success_after_expiry_rechecks_availability(self):
        before = self.counts()
        booking, payment = self.pending()
        Booking.objects.filter(id=booking.id).update(expires_at=timezone.now() - timedelta(minutes=1))
        call_command('release_expired_holds', verbosity=0)
        self.assertEqual(self.counts(), before)

        # Tickets still available: the late payment revives the booking and takes them again
        record_success(payment.id, booking.total_price, receipt='LATE00001')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')
        self.assertEqual(self.counts(), (before[0] + 2, before[1] - 2))

    def test_late_success_without_availability_is_flagged_for_refund(self):
        booking, payment = self.pending()
        Booking.objects.filter(id=booking.id).update(expires_at=timezone.now() - timedelta(minutes=1))
        call_command('release_expired_holds', verbosity=0)
        self.tier.refresh_from_db()
        self.tier.available = 1
        self.tier.save()
        record_success(payment.id, booking.total_price)
        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, 'expired')
        self.assertTrue(payment.needs_refund)
        self.tier.refresh_from_db()
        self.assertEqual(self.tier.available, 1)


def fake_response(data, status=200):
    res = mock.Mock(status_code=status)
    res.json.return_value = data
    return res


DARAJA = dict(
    PAYMENTS_MODE='live', MPESA_ENVIRONMENT='sandbox', MPESA_CONSUMER_KEY='key', MPESA_CONSUMER_SECRET='secret',
    MPESA_STK_SHORTCODE='174379', MPESA_PASSKEY='passkey', MPESA_C2B_SHORTCODE='600984',
    PAYMENT_CALLBACK_SECRET='s3cret-path', PUBLIC_API_URL='https://api.magivents.test', EMAIL_BACKEND=LOCMEM,
    DARAJA_ALLOWED_IPS=[],
)
OAUTH = {'access_token': 'token-1', 'expires_in': '3599'}


@override_settings(**DARAJA)
class DarajaStkTests(LifecycleBase):
    def start(self, phone='0712 345 678'):
        accepted = {'MerchantRequestID': '1-2', 'CheckoutRequestID': 'ws_CO_123', 'ResponseCode': '0',
                    'ResponseDescription': 'Success. Request accepted for processing'}
        with mock.patch('payments.providers.daraja.requests.get', return_value=fake_response(OAUTH)) as get, \
                mock.patch('payments.providers.daraja.requests.post', return_value=fake_response(accepted)) as post:
            res = self.book(phone=phone)
        return res, get, post

    def callback(self, body, secret='s3cret-path'):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f'/api/payments/hooks/stk/{secret}/', body, format='json')

    def stk_body(self, code=0, amount=None, checkout='ws_CO_123'):
        callback = {'MerchantRequestID': '1-2', 'CheckoutRequestID': checkout, 'ResultCode': code,
                    'ResultDesc': 'The service request is processed successfully.' if code == 0 else 'Request cancelled by user'}
        if code == 0:
            callback['CallbackMetadata'] = {'Item': [
                {'Name': 'Amount', 'Value': amount}, {'Name': 'MpesaReceiptNumber', 'Value': 'NLJ7RT61SV'},
                {'Name': 'TransactionDate', 'Value': 20261012102115}, {'Name': 'PhoneNumber', 'Value': 254712345678}]}
        return {'Body': {'stkCallback': callback}}

    def test_stk_push_request_and_callback(self):
        res, get, post = self.start()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['status'], 'pending')
        sent = post.call_args.kwargs['json']
        self.assertEqual(post.call_args.args[0], 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest')
        self.assertEqual(sent['PartyA'], '254712345678')
        self.assertEqual(sent['PhoneNumber'], '254712345678')
        self.assertEqual(sent['Amount'], int(res.data['totalPrice']))
        self.assertEqual(sent['CallBackURL'], 'https://api.magivents.test/api/payments/hooks/stk/s3cret-path/')
        self.assertLessEqual(len(sent['AccountReference']), 12)
        self.assertLessEqual(len(sent['TransactionDesc']), 13)
        self.assertNotIn('mpesa', sent['CallBackURL'].lower())
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer token-1')

        # A wrong path secret is ignored
        self.assertEqual(self.callback(self.stk_body(amount=res.data['totalPrice']), secret='guess').status_code, 404)
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'pending')

        ok = self.callback(self.stk_body(amount=res.data['totalPrice']))
        self.assertEqual(ok.json(), {'ResultCode': '0', 'ResultDesc': 'Accepted'})
        booking = Booking.objects.get(id=res.data['id'])
        self.assertEqual(booking.status, 'confirmed')
        self.assertEqual(booking.mpesa_receipt_number, 'NLJ7RT61SV')
        self.assertEqual(len(mail.outbox), 1)
        # Safaricom retries the same callback: nothing changes
        self.callback(self.stk_body(amount=res.data['totalPrice']))
        self.assertEqual(len(mail.outbox), 1)

    def test_cancelled_prompt_releases_tickets(self):
        before = self.counts()
        res, _, _ = self.start()
        self.callback(self.stk_body(code=1032))
        booking = Booking.objects.get(id=res.data['id'])
        self.assertEqual(booking.status, 'failed')
        self.assertEqual(self.counts(), before)
        status = self.status(res.data['id']).data
        self.assertIn('cancelled', status['payment']['failureReason'])

    def test_allowlisted_ips_only(self):
        res, _, _ = self.start()
        with override_settings(DARAJA_ALLOWED_IPS=['196.201.214.200']):
            self.assertEqual(self.callback(self.stk_body(amount=res.data['totalPrice'])).status_code, 404)
            with self.captureOnCommitCallbacks(execute=True):
                ok = self.client.post('/api/payments/hooks/stk/s3cret-path/', self.stk_body(amount=res.data['totalPrice']),
                                      format='json', REMOTE_ADDR='196.201.214.200')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'confirmed')

    def test_answered_prompt_shows_within_seconds_without_a_callback(self):
        res, _, _ = self.start()
        cancelled = {'ResponseCode': '0', 'ResultCode': '1032', 'ResultDesc': 'Request cancelled by user',
                     'CheckoutRequestID': 'ws_CO_123'}
        payments = Payment.objects.filter(booking_id=res.data['id'])
        payments.update(last_queried_at=None, created_at=timezone.now() - timedelta(seconds=2))
        with mock.patch('payments.providers.daraja.requests.post', return_value=fake_response(cancelled)) as post:
            self.assertEqual(self.status(res.data['id']).data['status'], 'pending')
        post.assert_not_called()  # too soon for anyone to have answered
        payments.update(created_at=timezone.now() - timedelta(seconds=6))
        with mock.patch('payments.providers.daraja.requests.post', return_value=fake_response(cancelled)):
            self.assertEqual(self.status(res.data['id']).data['status'], 'failed')

    def query_after(self, booking_id, seconds, result):
        Payment.objects.filter(booking_id=booking_id).update(
            last_queried_at=None, created_at=timezone.now() - timedelta(seconds=seconds))
        body = {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_123', **result}
        with mock.patch('payments.providers.daraja.requests.post', return_value=fake_response(body)):
            return self.status(booking_id).data

    def test_unfinished_answers_from_the_status_check_stay_pending(self):
        res, _, _ = self.start()
        # Safaricom says these while the attendee is still entering the PIN
        for code, desc in (('4999', 'The transaction is still under processing'), ('1037', 'No response from user.')):
            data = self.query_after(res.data['id'], 30, {'ResultCode': code, 'ResultDesc': desc})
            self.assertEqual(data['status'], 'pending', code)
        # Once the prompt can no longer be answered, "no response" is a failure
        data = self.query_after(res.data['id'], 95, {'ResultCode': '1037', 'ResultDesc': 'No response from user.'})
        self.assertEqual(data['status'], 'failed')

    def test_late_success_after_a_failure_still_gives_the_ticket(self):
        res, _, _ = self.start()
        self.query_after(res.data['id'], 95, {'ResultCode': '1037', 'ResultDesc': 'No response from user.'})
        ok = self.callback(self.stk_body(amount=res.data['totalPrice']))
        self.assertEqual(ok.status_code, 200)
        booking = Booking.objects.get(id=res.data['id'])
        self.assertEqual(booking.status, 'confirmed')
        self.assertTrue(booking.ticket_code)

    def test_expired_booking_keeps_asking_about_an_unanswered_payment(self):
        res, _, _ = self.start()
        Booking.objects.filter(id=res.data['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        call_command('release_expired_holds', verbosity=0)
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'expired')
        data = self.query_after(res.data['id'], 130, {'ResultCode': '0', 'ResultDesc': 'Processed successfully.'})
        self.assertEqual(data['status'], 'confirmed')

    def test_query_fallback_when_callback_never_arrives(self):
        res, _, _ = self.start()
        Payment.objects.filter(booking_id=res.data['id']).update(
            last_queried_at=None, created_at=timezone.now() - timedelta(minutes=1))
        query = {'ResponseCode': '0', 'ResultCode': '0', 'ResultDesc': 'The service request is processed successfully.',
                 'CheckoutRequestID': 'ws_CO_123'}
        with mock.patch('payments.providers.daraja.requests.post', return_value=fake_response(query)) as post:
            done = self.status(res.data['id'])
        self.assertEqual(post.call_args.args[0], 'https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query')
        self.assertEqual(post.call_args.kwargs['json']['CheckoutRequestID'], 'ws_CO_123')
        self.assertEqual(done.data['status'], 'confirmed')

    def test_daraja_refusal_marks_booking_failed(self):
        before = self.counts()
        refused = {'requestId': 'x', 'errorCode': '500.001.1001', 'errorMessage': 'Wrong credentials'}
        with mock.patch('payments.providers.daraja.requests.get', return_value=fake_response(OAUTH)), \
                mock.patch('payments.providers.daraja.requests.post', return_value=fake_response(refused, 500)):
            res = self.book()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['status'], 'failed')
        self.assertEqual(self.counts(), before)

    def test_invalid_phone_is_refused_before_holding_tickets(self):
        before = self.counts()
        res = self.book(phone='12345')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(self.counts(), before)


@override_settings(**DARAJA)
class DarajaPaybillTests(LifecycleBase):
    def post_hook(self, name, body):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f'/api/payments/hooks/{name}/s3cret-path/', body, format='json')

    def c2b(self, account, amount, trans='SJ12ABC345'):
        return {'TransactionType': 'Pay Bill', 'TransID': trans, 'TransTime': '20261012102115',
                'TransAmount': f'{amount:.2f}', 'BusinessShortCode': '600984', 'BillRefNumber': account,
                'InvoiceNumber': '', 'OrgAccountBalance': '', 'ThirdPartyTransID': '', 'MSISDN': '2547 ***** 678',
                'FirstName': 'NJERI'}

    def test_paybill_matched_on_account_number_and_amount(self):
        res = self.book(mode='paybill', phone='')
        self.assertEqual(res.status_code, 201, res.data)
        instructions = res.data['payment']['instructions']
        self.assertEqual(instructions['paybill'], '600984')
        account, total = instructions['accountNumber'], res.data['totalPrice']

        self.assertEqual(self.post_hook('c2b-validate', self.c2b('NOPE', total)).json()['ResultCode'], 'C2B00012')
        self.assertEqual(self.post_hook('c2b-validate', self.c2b(account, total - 1)).json()['ResultCode'], 'C2B00013')
        self.assertEqual(self.post_hook('c2b-validate', self.c2b(account.lower(), total)).json()['ResultCode'], '0')

        self.post_hook('c2b-confirm', self.c2b(account, total))
        booking = Booking.objects.get(id=res.data['id'])
        self.assertEqual(booking.status, 'confirmed')
        self.assertEqual(booking.mpesa_receipt_number, 'SJ12ABC345')

        # The same confirmation again is a no-op; a second, different payment is flagged for refund
        self.post_hook('c2b-confirm', self.c2b(account, total))
        self.assertFalse(Payment.objects.get(reference=account).needs_refund)
        self.post_hook('c2b-confirm', self.c2b(account, total, trans='SJ99XYZ000'))
        self.assertTrue(Payment.objects.get(reference=account).needs_refund)

    def test_underpayment_does_not_confirm(self):
        res = self.book(mode='paybill', phone='')
        account = res.data['payment']['instructions']['accountNumber']
        self.post_hook('c2b-confirm', self.c2b(account, res.data['totalPrice'] - 100))
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'pending')
        self.assertTrue(Payment.objects.get(reference=account).needs_refund)


PAYSTACK = dict(PAYMENTS_MODE='live', PAYSTACK_SECRET_KEY='sk_test_abc', EMAIL_BACKEND=LOCMEM,
                FRONTEND_URL='https://magivents.test')


@override_settings(**PAYSTACK)
class PaystackTests(LifecycleBase):
    def start(self):
        init = {'status': True, 'message': 'Authorization URL created',
                'data': {'authorization_url': 'https://checkout.paystack.com/abc', 'access_code': 'abc', 'reference': 'x'}}
        with mock.patch('payments.providers.paystack.requests.post', return_value=fake_response(init)) as post:
            res = self.book(method='card', phone='')
        return res, post

    def webhook(self, body, secret='sk_test_abc'):
        raw = json.dumps(body).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post('/api/payments/hooks/paystack/', raw, content_type='application/json',
                                    HTTP_X_PAYSTACK_SIGNATURE=signature)

    def verify(self, reference, amount_cents, status='success', currency='KES'):
        return {'status': True, 'message': 'Verification successful',
                'data': {'id': 4099260516, 'status': status, 'reference': reference, 'amount': amount_cents,
                         'currency': currency, 'gateway_response': 'Approved'}}

    def test_hosted_checkout_and_verified_webhook(self):
        res, post = self.start()
        self.assertEqual(res.data['status'], 'pending')
        self.assertEqual(res.data['payment']['authorizationUrl'], 'https://checkout.paystack.com/abc')
        sent = post.call_args.kwargs['json']
        self.assertEqual(sent['amount'], str(int(res.data['totalPrice'] * 100)))
        self.assertEqual(sent['currency'], 'KES')
        self.assertEqual(sent['callback_url'], f"https://magivents.test/?booking={res.data['id']}")
        reference = sent['reference']
        event = {'event': 'charge.success', 'data': {'reference': reference, 'status': 'success',
                                                     'amount': sent['amount'], 'currency': 'KES'}}

        # Unsigned or wrongly signed webhooks are rejected without touching the booking
        self.assertEqual(self.webhook(event, secret='wrong').status_code, 401)
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'pending')

        verified = self.verify(reference, int(sent['amount']))
        with mock.patch('payments.providers.paystack.requests.get', return_value=fake_response(verified)) as get:
            self.assertEqual(self.webhook(event).status_code, 200)
        self.assertEqual(get.call_args.args[0], f'https://api.paystack.co/transaction/verify/{reference}')
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'confirmed')
        self.assertEqual(len(mail.outbox), 1)

    def test_webhook_body_is_not_trusted_without_verify(self):
        res, post = self.start()
        reference = post.call_args.kwargs['json']['reference']
        event = {'event': 'charge.success', 'data': {'reference': reference, 'status': 'success', 'amount': 999999}}
        # The provider's own record says it is still processing
        verified = self.verify(reference, 999999, status='ongoing')
        with mock.patch('payments.providers.paystack.requests.get', return_value=fake_response(verified)):
            self.webhook(event)
        self.assertEqual(Booking.objects.get(id=res.data['id']).status, 'pending')

    def test_status_poll_verifies_after_redirect(self):
        res, post = self.start()
        reference = post.call_args.kwargs['json']['reference']
        verified = self.verify(reference, int(res.data['totalPrice'] * 100))
        with mock.patch('payments.providers.paystack.requests.get', return_value=fake_response(verified)):
            done = self.status(res.data['id'])
        self.assertEqual(done.data['status'], 'confirmed')


@override_settings(PAYMENTS_MODE='live', MPESA_CONSUMER_KEY='', PAYSTACK_SECRET_KEY='')
class MissingCredentialsTests(LifecycleBase):
    def test_live_mode_without_credentials_degrades_gracefully(self):
        config = self.client.get('/api/config/').data
        self.assertEqual(config['paymentsMode'], 'live')
        self.assertEqual(config['paymentMethods'], {'mpesaStk': False, 'mpesaPaybill': False, 'card': False})
        before = self.counts()
        res = self.book()
        self.assertEqual(res.status_code, 400)
        self.assertIn('not set up', res.data['error'])
        self.assertEqual(self.counts(), before)
        self.assertEqual(Booking.objects.count(), 0)
        # Free events still work
        free = Gathering.objects.get(id='nairobi-dev-meetup-payments')
        res = self.client.post('/api/bookings/', {'eventId': free.id, 'tierId': str(free.tiers.first().id),
                                                  'quantity': 1, 'attendeeName': 'A', 'attendeeEmail': 'a@x.com'},
                               format='json')
        self.assertEqual(res.data['status'], 'confirmed')



class PublicFailureReasonTests(APITestCase):
    def test_attendees_only_see_messages_written_for_them(self):
        from payments.serializers import public_failure_reason
        payment = Payment(failure_reason='The M-Pesa PIN was wrong.')
        self.assertEqual(public_failure_reason(payment), 'The M-Pesa PIN was wrong.')
        for internal in ('Paid KSh 499 but KSh 500 was due.', 'DS timeout user cannot be reached', 'Declined by issuer 05',
                         'Replaced by a new payment attempt.'):
            payment.failure_reason = internal
            self.assertEqual(public_failure_reason(payment), 'The payment was not completed.')
        payment.failure_reason = ''
        self.assertIsNone(public_failure_reason(payment))
