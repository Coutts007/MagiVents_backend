


from django.test import override_settings
from rest_framework.test import APITestCase


class PublicConfigTests(APITestCase):
    @override_settings(MPESA_PAYBILL_NUMBER='123456', MPESA_ACCOUNT_NAME='Test Events')
    def test_config_exposes_paybill_from_settings(self):
        res = self.client.get('/api/config/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['mpesaPaybill'], '123456')
        self.assertEqual(res.data['mpesaAccountName'], 'Test Events')

    def test_default_paybill_is_a_placeholder(self):
        self.assertEqual(self.client.get('/api/config/').data['mpesaPaybill'], '000000')


class ApiDocsTests(APITestCase):
    def test_schema_and_swagger_ui(self):
        schema = self.client.get('/api/schema/')
        self.assertEqual(schema.status_code, 200)
        self.assertIn(b'/api/gatherings/', schema.content)
        self.assertEqual(self.client.get('/api/docs/').status_code, 200)


from unittest import mock

from django.core.cache import cache

from accounts.models import User


class GeocodeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='g@x.com', email='g@x.com', password='pw-Geo-12345', role='curator')

    def test_requires_sign_in(self):
        self.assertEqual(self.client.get('/api/geocode/', {'q': 'KICC Nairobi'}).status_code, 401)

    def test_search_is_proxied_identified_and_cached(self):
        self.client.force_authenticate(self.user)
        upstream = mock.Mock(status_code=200)
        upstream.raise_for_status.return_value = None
        upstream.json.return_value = [
            {'lat': '-1.2889', 'lon': '36.8231', 'display_name': 'Kenyatta International Convention Centre, Nairobi'}]
        with mock.patch('core.geocoding.requests.get', return_value=upstream) as get:
            res = self.client.get('/api/geocode/', {'q': 'KICC Nairobi'})
            again = self.client.get('/api/geocode/', {'q': 'kicc   nairobi'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['results'][0], {'name': 'Kenyatta International Convention Centre, Nairobi',
                                                  'lat': -1.2889, 'lng': 36.8231})
        self.assertEqual(again.data, res.data)
        self.assertEqual(get.call_count, 1)  # the second search came from the cache
        params, headers = get.call_args.kwargs['params'], get.call_args.kwargs['headers']
        self.assertEqual((params['format'], params['countrycodes']), ('jsonv2', 'ke'))
        self.assertIn('MagiVents', headers['User-Agent'])

    def test_upstream_failure_degrades(self):
        import requests
        self.client.force_authenticate(self.user)
        with mock.patch('core.geocoding.requests.get', side_effect=requests.ConnectionError('down')):
            res = self.client.get('/api/geocode/', {'q': 'Somewhere'})
        self.assertEqual(res.status_code, 503)
        self.assertIn('Drag the pin', res.data['error'])
