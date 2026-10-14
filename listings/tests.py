import importlib

from django.apps import apps
from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from gatherings.models import Gathering
from gatherings.publishing import PublishBlocked
from gatherings.tests import event_payload, give_payout_details
from notifications.models import Notification
from payments.models import Payment
from payments.services import record_failure, record_success
from .models import CategoryFee, ListingFee, PlatformPaymentAccount
from .services import fee_for

MUSIC, BUSINESS, OTHERS = 'Entertainment & Music', 'Business & Entrepreneurship', 'Others'


def set_fee(category, amount):
    CategoryFee.objects.update_or_create(category=category, defaults={'amount': amount})


class ListingFeeTestCase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='a@x.com', email='admin@x.com', password='pw-Adm-12345',
                                              role='admin')
        self.organizer = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Org-12345',
                                                  role='curator', first_name='Wanjiku')
        give_payout_details(self.organizer)
        set_fee(MUSIC, 500)
        set_fee(BUSINESS, 800)
        set_fee(OTHERS, 100)
        PlatformPaymentAccount.objects.create(pk=1, method='paybill', business_number='247247',
                                              account_name='MagiVents Ltd', instructions='Use the reference.')
        self.client.force_authenticate(self.organizer)

    def create(self, **overrides):
        res = self.client.post('/api/gatherings/', event_payload(**{'category': MUSIC, **overrides}), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['id']

    def publish(self, event_id):
        return self.client.patch(f'/api/gatherings/{event_id}/', {'status': 'published'}, format='json')

    def pay(self, event_id, phone='0712345678', **extra):
        return self.client.post(f'/api/gatherings/{event_id}/listing-fee/pay/',
                                {'mpesaPhoneNumber': phone, **extra}, format='json')

    def status_of(self, event_id):
        return Gathering.objects.get(id=event_id).status


class NewEventsAreDraftsTests(ListingFeeTestCase):
    def test_new_event_is_a_draft_with_a_fee_and_the_admins_are_told(self):
        with self.captureOnCommitCallbacks(execute=True):
            event_id = self.create(status='published')
        self.assertEqual(self.status_of(event_id), 'draft')
        fee = ListingFee.objects.get(gathering_id=event_id)
        self.assertEqual(fee.status, 'unpaid')
        note = Notification.objects.get(recipient=self.admin)
        self.assertEqual(note.kind, 'listing_fee_due')
        self.assertIn('KSh 500', note.body)
        self.assertEqual([m.to for m in mail.outbox], [['admin@x.com']])
        # The organizer sees the fee on their event
        event = self.client.get(f'/api/gatherings/{event_id}/').data
        self.assertEqual((event['listingFee']['status'], event['listingFee']['amountDue']), ('unpaid', 500))

    def test_no_fee_no_notification_and_publishes_directly(self):
        event_id = self.create(category='Political')
        self.assertFalse(Notification.objects.exists())
        self.assertEqual(self.publish(event_id).status_code, 200)
        self.assertEqual(ListingFee.objects.get(gathering_id=event_id).status, 'exempt')


class PublishGateTests(ListingFeeTestCase):
    """An unpaid event cannot be published by any path."""

    def setUp(self):
        super().setUp()
        self.event_id = self.create()

    def assert_blocked(self, res):
        self.assertEqual(res.status_code, 402, res.data)
        self.assertEqual((res.data['code'], res.data['amountDue']), ('listing_fee_due', 500))
        self.assertIn('KSh 500', res.data['error'])
        self.assertEqual(self.status_of(self.event_id), 'draft')

    def test_partial_update(self):
        self.assert_blocked(self.publish(self.event_id))

    def test_full_update(self):
        event = self.client.get(f'/api/gatherings/{self.event_id}/').data
        event['status'] = 'published'
        self.assert_blocked(self.client.put(f'/api/gatherings/{self.event_id}/', event, format='json'))

    def test_sold_out_status_is_also_public(self):
        self.assert_blocked(self.client.patch(f'/api/gatherings/{self.event_id}/', {'status': 'sold_out'}, format='json'))

    def test_create_never_publishes(self):
        res = self.client.post('/api/gatherings/', event_payload(category=MUSIC, status='published'), format='json')
        self.assertEqual(res.data['status'], 'draft')

    @override_settings(ADMIN_CONSOLE_PATH='a-very-long-console-secret')
    def test_staff_console_status_action(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(f'/api/console/a-very-long-console-secret/events/{self.event_id}/status/',
                               {'status': 'published', 'note': 'go'}, format='json')
        self.assert_blocked(res)

    def test_model_backstop(self):
        gathering = Gathering.objects.get(id=self.event_id)
        gathering.status = 'published'
        with self.assertRaises(PublishBlocked):
            gathering.save()
        self.assertEqual(self.status_of(self.event_id), 'draft')

    def test_free_events_pay_the_fee_too(self):
        free_id = self.create(isFree=True)
        res = self.publish(free_id)
        self.assertEqual(res.status_code, 402)


@override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0)
class PayToPublishTests(ListingFeeTestCase):
    def test_paying_publishes_and_notifies_once(self):
        event_id = self.create()
        Notification.objects.all().delete()
        res = self.pay(event_id)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['status'], res.data['eventStatus'], res.data['amountDue']), ('paid', 'published', 0))
        fee = ListingFee.objects.get(gathering_id=event_id)
        self.assertEqual((fee.amount_paid, fee.cleared_category), (500, MUSIC))
        payment = Payment.objects.get(listing_fee=fee)
        self.assertEqual((payment.purpose, payment.status, payment.amount), ('listing_fee', 'succeeded', 500))
        self.assertEqual(Notification.objects.filter(kind='listing_fee_paid').count(), 1)

        # A repeated confirmation records nothing twice
        record_success(payment.id, 500, receipt='AGAIN')
        record_success(payment.id, 500)
        fee.refresh_from_db()
        self.assertEqual(fee.amount_paid, 500)
        self.assertEqual(Notification.objects.filter(kind='listing_fee_paid').count(), 1)
        self.assertEqual(self.status_of(event_id), 'published')

    def test_charged_once_even_after_unpublishing_and_a_fee_change(self):
        event_id = self.create()
        self.pay(event_id)
        set_fee(MUSIC, 5000)
        self.assertEqual(self.client.patch(f'/api/gatherings/{event_id}/', {'status': 'draft'}, format='json').status_code, 200)
        self.assertEqual(self.publish(event_id).status_code, 200)
        self.assertEqual(Payment.objects.filter(listing_fee__gathering_id=event_id).count(), 1)
        self.assertEqual(ListingFee.objects.get(gathering_id=event_id).amount_paid, 500)

    def test_category_upgrade_pays_the_difference_first(self):
        event_id = self.create()
        self.pay(event_id)
        res = self.client.patch(f'/api/gatherings/{event_id}/', {'category': BUSINESS}, format='json')
        self.assertEqual(res.status_code, 402)
        self.assertEqual((res.data['code'], res.data['amountDue'], res.data['targetCategory']),
                         ('category_upgrade_due', 300, BUSINESS))
        self.assertEqual(Gathering.objects.get(id=event_id).category, MUSIC)

        res = self.pay(event_id, category=BUSINESS)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['category'], BUSINESS)
        fee = ListingFee.objects.get(gathering_id=event_id)
        self.assertEqual((fee.amount_paid, fee.cleared_amount), (800, 800))

        # Cheaper category: allowed, no refund; and moving back up to what was paid costs nothing
        self.assertEqual(self.client.patch(f'/api/gatherings/{event_id}/', {'category': OTHERS}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(f'/api/gatherings/{event_id}/', {'category': BUSINESS}, format='json').status_code, 200)
        fee.refresh_from_db()
        self.assertEqual(fee.amount_paid, 800)

    def test_unpaid_draft_can_change_category_freely(self):
        event_id = self.create()
        self.assertEqual(self.client.patch(f'/api/gatherings/{event_id}/', {'category': BUSINESS}, format='json').status_code, 200)
        self.assertEqual(self.client.get(f'/api/gatherings/{event_id}/').data['listingFee']['amountDue'], 800)

    def test_failed_payment_leaves_a_draft_and_tells_the_admins(self):
        event_id = self.create()
        res = self.pay(event_id, phone='0712341032')
        self.assertEqual(res.data['status'], 'unpaid')
        self.assertEqual(res.data['payment']['status'], 'failed')
        self.assertEqual(self.status_of(event_id), 'draft')
        self.assertTrue(Notification.objects.filter(kind='listing_fee_failed', recipient=self.admin).exists())

    def test_nothing_to_pay_and_other_rules_first(self):
        free_category = self.create(category='Political')
        self.assertEqual(self.pay(free_category).status_code, 400)
        no_details = self.create()
        self.organizer.payout_accounts.all().delete()
        res = self.pay(no_details)
        self.assertEqual(res.data['code'], 'payment_details_missing')
        self.assertFalse(Payment.objects.filter(purpose='listing_fee').exists())

    def test_only_the_owner_can_pay_or_see_the_fee(self):
        event_id = self.create()
        self.client.force_authenticate(User.objects.create_user(username='z@x.com', email='z@x.com',
                                                                password='pw-Zed-12345', role='curator'))
        self.assertEqual(self.pay(event_id).status_code, 403)
        self.assertEqual(self.client.get(f'/api/gatherings/{event_id}/listing-fee/').status_code, 403)

    def test_suspended_organizer_cannot_pay_or_publish(self):
        event_id = self.create()
        self.organizer.is_suspended = True
        self.organizer.save()
        self.assertEqual(self.pay(event_id).status_code, 403)
        self.assertEqual(self.publish(event_id).status_code, 403)

    @override_settings(PAYMENTS_MODE='live')
    def test_live_mode_listing_fees_are_paid_by_hand(self):
        event_id = self.create()
        res = self.pay(event_id)
        self.assertEqual((res.status_code, res.data['code']), (503, 'listing_fee_offline'))
        state = self.client.get(f'/api/gatherings/{event_id}/listing-fee/').data
        self.assertEqual((state['onlinePayment'], state['platform']['businessNumber']), (False, '247247'))
        # Without a fixed account number, the event's own reference is the Paybill account
        self.assertEqual(state['platform']['accountNumber'], state['reference'])

    def test_event_with_a_paid_fee_cannot_be_deleted(self):
        event_id = self.create()
        self.pay(event_id)
        self.assertEqual(self.client.delete(f'/api/gatherings/{event_id}/').status_code, 403)
        failed_id = self.create()
        self.pay(failed_id, phone='0712341032')
        self.assertEqual(self.client.delete(f'/api/gatherings/{failed_id}/').status_code, 204)


class PendingPaymentTests(ListingFeeTestCase):
    @override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=600)
    def test_pending_then_confirmed_by_polling(self):
        event_id = self.create()
        res = self.pay(event_id)
        self.assertEqual(res.data['status'], 'pending')
        blocked = self.publish(event_id)
        self.assertEqual((blocked.status_code, blocked.data['code']), (402, 'listing_fee_pending'))
        # The browser never decides: only the server's confirmation publishes
        payment = Payment.objects.get(purpose='listing_fee')
        with override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0):
            state = self.client.get(f'/api/gatherings/{event_id}/listing-fee/').data
        self.assertEqual((state['status'], state['eventStatus']), ('paid', 'published'))
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'succeeded')

    @override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=600)
    def test_late_failure_after_success_changes_nothing(self):
        event_id = self.create()
        self.pay(event_id)
        payment = Payment.objects.get(purpose='listing_fee')
        record_success(payment.id, 500)
        record_failure(payment.id, 'late failure')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'succeeded')
        self.assertEqual(self.status_of(event_id), 'published')
        self.assertFalse(Notification.objects.filter(kind='listing_fee_failed').exists())

    @override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=600)
    def test_wrong_amount_never_publishes(self):
        event_id = self.create()
        self.pay(event_id)
        payment = Payment.objects.get(purpose='listing_fee')
        record_success(payment.id, 499)
        payment.refresh_from_db()
        self.assertTrue(payment.needs_refund)
        self.assertEqual(self.status_of(event_id), 'draft')


class GrandfatheredEventsTests(APITestCase):
    def test_seeded_events_count_as_paid(self):
        set_fee(MUSIC, 999)
        call_command('seed_gatherings', verbosity=0)
        self.assertFalse(Gathering.objects.filter(status='draft').exists())
        self.assertFalse(ListingFee.objects.exclude(status='paid').exists())
        self.assertTrue(ListingFee.objects.filter(grandfathered=True).exists())

    def test_data_migration_marks_existing_events_paid(self):
        call_command('seed_gatherings', verbosity=0)
        ListingFee.objects.all().delete()
        importlib.import_module('listings.migrations.0002_existing_events_count_as_paid').forwards(apps, None)
        self.assertEqual(ListingFee.objects.count(), Gathering.objects.count())
        self.assertFalse(ListingFee.objects.exclude(status='paid').exists())

    def test_category_without_a_row_has_no_fee(self):
        self.assertEqual(fee_for('Arts & Culture'), 0)
