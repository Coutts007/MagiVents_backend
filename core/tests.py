


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
