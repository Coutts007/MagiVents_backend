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
