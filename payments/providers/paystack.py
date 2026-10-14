"""
Paystack hosted checkout for cards (paystack.com/docs, checked October 2026). No card data touches MagiVents:

- Initialize: POST /transaction/initialize  {email, amount (KES cents, as a string), currency, reference,
              callback_url, metadata, channels} -> data.authorization_url
- Verify:     GET  /transaction/verify/<reference> -> data.status ('success', 'failed', 'abandoned', ...)
- Webhook:    header x-paystack-signature = hex HMAC-SHA512 of the raw body, keyed with the secret key
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal

import requests
from django.conf import settings

from ..services import PaymentError
from .base import PaymentProvider, QueryResult

logger = logging.getLogger(__name__)

API = 'https://api.paystack.co'
TIMEOUT = 20
UNAVAILABLE = 'Card payments are not responding right now. Please try again shortly, or pay with M-Pesa.'
FAILED_STATUSES = {'failed', 'reversed'}


def _headers():
    return {'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'}


def valid_signature(raw_body, signature):
    if not settings.PAYSTACK_SECRET_KEY or not signature:
        return False
    expected = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_transaction(reference):
    try:
        res = requests.get(f'{API}/transaction/verify/{reference}', headers=_headers(), timeout=TIMEOUT)
        return res.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('Paystack verify failed for %s: %s', reference, exc)
        raise PaymentError(UNAVAILABLE) from exc


def result_from_verify(payment, body):
    data = body.get('data') or {}
    status = data.get('status')
    if not body.get('status') or data.get('reference') != payment.reference:
        return QueryResult('pending', raw=body)
    if status == 'success':
        if data.get('currency') != payment.currency:
            return QueryResult('failed', reason='The card payment was made in the wrong currency.', raw=body)
        return QueryResult('succeeded', amount=Decimal(data.get('amount', 0)) / 100,
                           provider_reference=str(data.get('id', '')), raw=body)
    if status in FAILED_STATUSES:
        # The gateway's own text stays in the raw payload for staff
        return QueryResult('failed', reason='The card payment failed.', raw=body)
    # abandoned / ongoing / pending / processing / queued: the attendee may still finish on Paystack
    return QueryResult('pending', raw=body)


class PaystackProvider(PaymentProvider):
    name = 'paystack'
    method = 'card'
    query_interval = 10
    not_configured_message = 'Card payments are not set up yet. Please pay with M-Pesa.'

    def is_configured(self):
        return bool(settings.PAYSTACK_SECRET_KEY)

    def initiate(self, payment):
        booking = payment.booking
        body = {
            'email': booking.attendee_email,
            'amount': str(int(payment.amount * 100)),  # KES is charged in cents
            'currency': payment.currency,
            'reference': payment.reference,
            'callback_url': f"{settings.FRONTEND_URL.rstrip('/')}/?booking={booking.id}",
            'metadata': json.dumps({'booking_id': str(booking.id), 'event': booking.gathering.title}),
            'channels': ['card'],
        }
        try:
            res = requests.post(f'{API}/transaction/initialize', json=body, headers=_headers(), timeout=TIMEOUT)
            data = res.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error('Paystack initialize failed: %s', exc)
            raise PaymentError(UNAVAILABLE) from exc
        payment.log_payload('initialize', data)
        if not data.get('status') or not (data.get('data') or {}).get('authorization_url'):
            logger.warning('Paystack refused payment %s: %s', payment.id, data)
            raise PaymentError('The card payment could not be started. Please try again or pay with M-Pesa.')
        payment.authorization_url = data['data']['authorization_url']

    def query(self, payment):
        return result_from_verify(payment, verify_transaction(payment.reference))

    def instructions(self, payment):
        return {'authorizationUrl': payment.authorization_url}
