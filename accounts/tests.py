from unittest import mock

from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import User

PNG_DATA_URL = (
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
)


class AuthFlowTests(APITestCase):
    def register(self, email='ada@example.com', password='Sturdy-pass-42'):
        return self.client.post('/api/auth/register/', {'name': 'Ada Lovelace', 'email': email, 'password': password}, format='json')

    def test_register_login_and_profile(self):
        res = self.register(email='Ada@Example.com')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['user']['name'], 'Ada Lovelace')
        self.assertEqual(res.data['user']['email'], 'ada@example.com')
        # No stock photo: the frontend shows initials until one is uploaded
        self.assertEqual(res.data['user']['avatarUrl'], '')
        self.assertIn('access', res.data['tokens'])

        res = self.client.post('/api/auth/login/', {'email': 'ADA@example.com', 'password': 'Sturdy-pass-42'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['tokens']['access']}")

        res = self.client.get('/api/auth/profile/')
        self.assertEqual(res.data['authProvider'], 'email')

    def test_duplicate_and_weak_registration_rejected(self):
        self.register()
        self.assertEqual(self.register().status_code, 400)
        self.assertEqual(self.register(email='b@example.com', password='123').status_code, 400)

    def test_wrong_password(self):
        self.register()
        res = self.client.post('/api/auth/login/', {'email': 'ada@example.com', 'password': 'nope'}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_profile_update_with_uploaded_avatar(self):
        token = self.register().data['tokens']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        res = self.client.patch('/api/auth/profile/', {'name': 'Ada L.', 'city': 'London', 'avatarUrl': PNG_DATA_URL}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['name'], 'Ada L.')
        self.assertTrue(res.data['avatarUrl'].startswith('http://testserver/media/avatars/'))
        User.objects.get(email='ada@example.com').avatar.delete()

    def test_password_change(self):
        token = self.register().data['tokens']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        bad = self.client.post('/api/auth/password/change/', {'currentPassword': 'x', 'newPassword': 'Another-pass-99'}, format='json')
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post('/api/auth/password/change/', {'currentPassword': 'Sturdy-pass-42', 'newPassword': 'Another-pass-99'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(User.objects.get(email='ada@example.com').check_password('Another-pass-99'))

    def test_password_reset_sends_email(self):
        self.register()
        res = self.client.post('/api/auth/password/reset/', {'email': 'ada@example.com'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/accounts/reset/', mail.outbox[0].body)


@override_settings(GOOGLE_OAUTH_CLIENT_ID='test-client-id')
class GoogleAuthTests(APITestCase):
    def test_requires_credential(self):
        res = self.client.post('/api/auth/google/', {'email': 'victim@example.com'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(User.objects.exists())

    @mock.patch('accounts.views.id_token.verify_oauth2_token', side_effect=ValueError('bad'))
    def test_rejects_invalid_token(self, _):
        res = self.client.post('/api/auth/google/', {'credential': 'forged'}, format='json')
        self.assertEqual(res.status_code, 400)

    @mock.patch('accounts.views.id_token.verify_oauth2_token')
    def test_creates_user_from_verified_token(self, verify):
        verify.return_value = {'email': 'g@example.com', 'email_verified': True, 'name': 'G User', 'sub': '123', 'picture': 'https://x/y.png'}
        res = self.client.post('/api/auth/google/', {'credential': 'valid'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['user']['authProvider'], 'google')
        self.assertEqual(res.data['user']['avatarUrl'], 'https://x/y.png')
        self.assertFalse(User.objects.get(email='g@example.com').has_usable_password())

    @override_settings(GOOGLE_OAUTH_CLIENT_ID='')
    def test_unconfigured_server(self):
        res = self.client.post('/api/auth/google/', {'credential': 'anything'}, format='json')
        self.assertEqual(res.status_code, 503)


class RoleTests(APITestCase):
    def test_admin_role_follows_is_staff_and_superusers(self):
        root = User.objects.create_superuser(username='root@x.com', email='root@x.com', password='pw-Root-123')
        self.assertEqual((root.role, root.is_staff), ('admin', True))

        user = User.objects.create_user(username='u@x.com', email='u@x.com', password='pw-User-123')
        user.role = 'admin'
        user.save(update_fields=['role'])
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

        # Unticking "staff status" in the Django admin demotes; ticking it promotes
        user.is_staff = False
        user.save()
        self.assertEqual(user.role, 'patron')
        user.is_staff = True
        user.save()
        self.assertEqual(User.objects.get(pk=user.pk).role, 'admin')

        staff = User.objects.create_user(username='s@x.com', email='s@x.com', password='pw-Staff-123', is_staff=True)
        self.assertEqual(staff.role, 'admin')

    def test_become_organizer_never_demotes_an_admin(self):
        admin = User.objects.create_user(username='a@x.com', email='a@x.com', password='pw-Admin-123', role='admin')
        self.client.force_authenticate(admin)
        res = self.client.post('/api/auth/become-organizer/')
        self.assertEqual(res.data['role'], 'admin')
        self.assertTrue(User.objects.get(pk=admin.pk).is_staff)

    def test_data_migration_makes_staff_admins_and_owners_organizers(self):
        import importlib
        from django.apps import apps
        from gatherings.models import Gathering

        staff = User.objects.create_user(username='st@x.com', email='st@x.com', password='pw-Staff-123')
        owner = User.objects.create_user(username='ow@x.com', email='ow@x.com', password='pw-Owner-123')
        User.objects.filter(pk=staff.pk).update(is_staff=True, role='patron')
        Gathering.objects.create(organizer=owner, title='Old', category='Others', description='d', date_display='d',
                                 iso_date='2030-01-01', time_display='t', venue_name='v', status='draft')
        User.objects.filter(pk=owner.pk).update(role='patron')

        importlib.import_module('accounts.migrations.0004_sync_admins_and_organizers').forwards(apps, None)
        self.assertEqual(User.objects.get(pk=staff.pk).role, 'admin')
        self.assertEqual(User.objects.get(pk=owner.pk).role, 'curator')

    def test_profile_reports_suspension(self):
        user = User.objects.create_user(username='c@x.com', email='c@x.com', password='pw-Cur-12345', role='curator',
                                        is_suspended=True, suspension_reason='Unpaid venue')
        self.client.force_authenticate(user)
        res = self.client.get('/api/auth/profile/')
        self.assertEqual((res.data['isSuspended'], res.data['suspensionReason']), (True, 'Unpaid venue'))


class SignInThrottleTests(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        User.objects.create_user(username='t@x.com', email='t@x.com', password='pw-Right-123')

    @override_settings(REST_FRAMEWORK={**__import__('django.conf').conf.settings.REST_FRAMEWORK,
                                       'DEFAULT_THROTTLE_RATES': {'login_email': '3/minute', 'login_ip': '100/minute',
                                                                  'register_ip': '100/hour'}})
    def test_guessing_one_account_is_limited(self):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import SimpleRateThrottle
        with mock.patch.object(SimpleRateThrottle, 'THROTTLE_RATES', api_settings.DEFAULT_THROTTLE_RATES):
            for _ in range(3):
                res = self.client.post('/api/auth/login/', {'email': 't@x.com', 'password': 'wrong'}, format='json')
                self.assertEqual(res.status_code, 401)
            res = self.client.post('/api/auth/login/', {'email': 'T@x.com ', 'password': 'pw-Right-123'}, format='json')
            self.assertEqual(res.status_code, 429)
            # Other accounts are unaffected
            res = self.client.post('/api/auth/login/', {'email': 'other@x.com', 'password': 'x'}, format='json')
            self.assertEqual(res.status_code, 401)
            res = self.client.post('/api/auth/token/', {'email': 't@x.com', 'password': 'pw-Right-123'}, format='json')
            self.assertEqual(res.status_code, 429)


@override_settings(AUTH_USER_CACHE_SECONDS=60)
class CachedSignInTests(APITestCase):
    """Signed-in requests use a cached copy of the user, which is dropped as soon as the user changes."""

    def setUp(self):
        from django.core.cache import cache
        from rest_framework_simplejwt.tokens import RefreshToken
        cache.clear()
        self.user = User.objects.create_user(username='c@x.com', email='c@x.com', password='pw-Cache-123')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.user).access_token}')

    def test_changes_to_the_user_apply_to_the_next_request(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.assertEqual(self.client.get('/api/auth/profile/').status_code, 200)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get('/api/auth/profile/').status_code, 200)
        self.assertEqual(len(queries), 0)  # served from the cached copy

        self.user.first_name = 'Changed'
        with self.captureOnCommitCallbacks(execute=True):
            self.user.save()
        self.assertEqual(self.client.get('/api/auth/profile/').data['name'], 'Changed')

        self.user.is_active = False
        with self.captureOnCommitCallbacks(execute=True):
            self.user.save()
        self.assertEqual(self.client.get('/api/auth/profile/').status_code, 401)
