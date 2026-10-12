from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from payments.providers.daraja import DarajaPaybillProvider, callback_url, register_c2b_urls
from payments.services import PaymentError


class Command(BaseCommand):
    help = 'Register the Paybill validation and confirmation URLs with Safaricom Daraja (once per environment).'

    def handle(self, *args, **options):
        if not DarajaPaybillProvider().is_configured():
            raise CommandError('Set MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET, MPESA_C2B_SHORTCODE, '
                               'PAYMENT_CALLBACK_SECRET and PUBLIC_API_URL first.')
        self.stdout.write(f'Environment: {settings.MPESA_ENVIRONMENT}')
        self.stdout.write(f'Confirmation URL: {callback_url("c2b-confirm")}')
        self.stdout.write(f'Validation URL:   {callback_url("c2b-validate")}')
        try:
            data = register_c2b_urls()
        except PaymentError as exc:
            raise CommandError(str(exc))
        self.stdout.write(str(data))
