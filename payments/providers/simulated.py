"""A stand-in gateway for demos and development: no credentials, same pending -> confirmed lifecycle."""
import random
import string

from django.conf import settings
from django.utils import timezone

from .base import PaymentProvider, QueryResult

# Phone numbers ending in these digits make the simulated payment fail, to demo the failure path
SIMULATED_FAILURES = {
    '1032': 'You cancelled the M-Pesa request.',
    '2001': 'The M-Pesa PIN was wrong.',
}


def simulated_receipt():
    return 'S' + ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(9))


class SimulatedProvider(PaymentProvider):
    name = 'simulated'
    query_interval = 0

    def __init__(self, method, mpesa_mode='stk'):
        self.method = method
        self.mpesa_mode = mpesa_mode

    def initiate(self, payment):
        payment.provider_reference = f'SIM-{payment.reference}'

    def query(self, payment):
        """The fake provider "completes" the payment once the simulated delay has passed."""
        elapsed = (timezone.now() - payment.created_at).total_seconds()
        if elapsed < settings.PAYMENTS_SIMULATED_DELAY_SECONDS:
            return QueryResult('pending')
        for suffix, reason in SIMULATED_FAILURES.items():
            if payment.method == 'mpesa' and payment.phone_number.endswith(suffix):
                return QueryResult('failed', reason=reason, raw={'simulated': True, 'result': reason})
        receipt = simulated_receipt() if payment.method == 'mpesa' else ''
        return QueryResult('succeeded', amount=payment.amount, receipt=receipt,
                           provider_reference=payment.provider_reference, raw={'simulated': True, 'receipt': receipt})

    def instructions(self, payment):
        data = {'simulated': True}
        if payment.method == 'mpesa' and payment.booking.mpesa_mode == 'paybill':
            data.update(paybill=settings.MPESA_PAYBILL_NUMBER, accountNumber=payment.reference)
        return data
