"""
Safaricom Daraja (M-Pesa). Endpoints and payloads follow developer.safaricom.co.ke (checked October 2026):

- OAuth:       GET  /oauth/v1/generate?grant_type=client_credentials   (Basic auth, token ~1 hour)
- STK Push:    POST /mpesa/stkpush/v1/processrequest                     -> CheckoutRequestID
- STK Query:   POST /mpesa/stkpushquery/v1/query                         -> ResultCode (string)
- C2B:         POST /mpesa/c2b/v2/registerurl  (validation + confirmation URLs, matched on BillRefNumber)

Daraja does not sign callbacks, so the callback URLs carry an unguessable path secret, can be limited
to Safaricom's published IPs, and are always matched against a stored payment and amount.
"""
import base64
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

from ..services import PaymentError
from .base import PaymentProvider, QueryResult

logger = logging.getLogger(__name__)

HOSTS = {'sandbox': 'https://sandbox.safaricom.co.ke', 'production': 'https://api.safaricom.co.ke'}
TOKEN_CACHE_KEY = 'daraja:access_token'
TIMEOUT = 20
UNAVAILABLE = 'M-Pesa is not responding right now. Please try again in a minute, or pay by card.'

# Safaricom's published callback source IPs (developer.safaricom.co.ke/apis/GettingStarted)
SAFARICOM_CALLBACK_IPS = [
    '196.201.214.200', '196.201.214.206', '196.201.213.114', '196.201.214.207', '196.201.214.208',
    '196.201.213.44', '196.201.212.127', '196.201.212.138', '196.201.212.129', '196.201.212.136',
    '196.201.212.74', '196.201.212.69',
]

# STK callback ResultCodes worth a friendlier message
STK_RESULT_MESSAGES = {
    1: 'Your M-Pesa balance was not enough for this payment.',
    1032: 'The M-Pesa request was cancelled or not answered in time.',
    1037: 'Your phone could not be reached. Check it is on and has signal, then try again.',
    2001: 'The M-Pesa PIN was wrong.',
    1019: 'The M-Pesa request expired.',
}


def base_url():
    return HOSTS.get(settings.MPESA_ENVIRONMENT, HOSTS['sandbox'])


def callback_url(name):
    return f"{settings.PUBLIC_API_URL.rstrip('/')}/api/payments/hooks/{name}/{settings.PAYMENT_CALLBACK_SECRET}/"


def access_token():
    """One shared token: Daraja invalidates the previous token whenever a new one is issued."""
    token = cache.get(TOKEN_CACHE_KEY)
    if token:
        return token
    try:
        res = requests.get(
            f'{base_url()}/oauth/v1/generate', params={'grant_type': 'client_credentials'},
            auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET), timeout=TIMEOUT,
        )
        res.raise_for_status()
        data = res.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('Daraja OAuth failed: %s', exc)
        raise PaymentError(UNAVAILABLE) from exc
    token = data['access_token']
    cache.set(TOKEN_CACHE_KEY, token, max(60, int(data.get('expires_in', 3599)) - 120))
    return token


def daraja_post(path, body):
    try:
        res = requests.post(f'{base_url()}{path}', json=body, timeout=TIMEOUT,
                            headers={'Authorization': f'Bearer {access_token()}'})
        data = res.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('Daraja %s failed: %s', path, exc)
        raise PaymentError(UNAVAILABLE) from exc
    if res.status_code == 401 or data.get('errorCode') in ('404.001.03', '404.001.04'):
        cache.delete(TOKEN_CACHE_KEY)
    return res, data


def stk_password(timestamp):
    raw = f'{settings.MPESA_STK_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}'
    return base64.b64encode(raw.encode()).decode()


def daraja_timestamp():
    return datetime.now(ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')


def _credentials_present():
    return bool(settings.MPESA_CONSUMER_KEY and settings.MPESA_CONSUMER_SECRET
                and settings.PAYMENT_CALLBACK_SECRET and settings.PUBLIC_API_URL)


class DarajaStkProvider(PaymentProvider):
    """Lipa na M-Pesa Online: a payment prompt on the attendee's phone."""
    name = 'mpesa_stk'
    method = 'mpesa'
    # The callback usually arrives first; when it cannot (slow, or no public URL) the attendee should still see
    # the result within seconds of answering the prompt. Unanswered prompts just report "still processing".
    query_interval = 4
    min_query_age = 5
    not_configured_message = 'M-Pesa prompts are not set up yet. Please pay by Paybill or card.'

    def is_configured(self):
        return _credentials_present() and bool(settings.MPESA_STK_SHORTCODE and settings.MPESA_PASSKEY)

    def initiate(self, payment):
        timestamp = daraja_timestamp()
        body = {
            'BusinessShortCode': settings.MPESA_STK_SHORTCODE,
            'Password': stk_password(timestamp),
            'Timestamp': timestamp,
            'TransactionType': settings.MPESA_STK_TRANSACTION_TYPE,
            'Amount': int(payment.amount),
            'PartyA': payment.phone_number,
            'PartyB': settings.MPESA_STK_PARTY_B or settings.MPESA_STK_SHORTCODE,
            'PhoneNumber': payment.phone_number,
            'CallBackURL': callback_url('stk'),
            'AccountReference': payment.reference[:12],  # shown on the prompt; max 12 characters
            'TransactionDesc': 'Event ticket',            # max 13 characters
        }
        res, data = daraja_post('/mpesa/stkpush/v1/processrequest', body)
        payment.log_payload('stk_request', {k: v for k, v in data.items()})
        if str(data.get('ResponseCode')) != '0' or not data.get('CheckoutRequestID'):
            logger.warning('STK push refused for payment %s: %s', payment.id, data)
            message = data.get('errorMessage') or data.get('ResponseDescription') or ''
            if 'already in process' in message.lower():
                raise PaymentError('An M-Pesa request is already open on that phone. Wait a minute and try again.')
            raise PaymentError('M-Pesa could not send the payment request. Check the number and try again.')
        payment.provider_reference = data['CheckoutRequestID']

    def query(self, payment):
        """Fallback when the callback is slow or never arrives (asked at most every `query_interval` s)."""
        if not payment.provider_reference:
            return None
        timestamp = daraja_timestamp()
        res, data = daraja_post('/mpesa/stkpushquery/v1/query', {
            'BusinessShortCode': settings.MPESA_STK_SHORTCODE,
            'Password': stk_password(timestamp),
            'Timestamp': timestamp,
            'CheckoutRequestID': payment.provider_reference,
        })
        if 'ResultCode' not in data:
            # errorCode responses (e.g. still being processed): ask again later
            return QueryResult('pending', raw=data)
        code = int(data['ResultCode'])
        if code == 0:
            # The query has no amount or receipt; an STK prompt can only charge the amount we requested
            return QueryResult('succeeded', amount=payment.amount, provider_reference=payment.provider_reference,
                               raw=data)
        # Unknown codes get the generic message; Safaricom's own text stays in the raw payload for staff
        return QueryResult('failed', reason=STK_RESULT_MESSAGES.get(code, ''), raw=data)

    def instructions(self, payment):
        return {'phoneNumber': payment.phone_number}


class DarajaPaybillProvider(PaymentProvider):
    """The attendee pays our Paybill with the account number we give; C2B callbacks confirm it."""
    name = 'mpesa_paybill'
    method = 'mpesa'
    not_configured_message = 'Paybill payments are not set up yet. Please use the M-Pesa prompt or card.'

    def is_configured(self):
        return _credentials_present() and bool(settings.MPESA_C2B_SHORTCODE)

    def initiate(self, payment):
        # Nothing to call: the account number (payment.reference) is what the confirmation is matched on
        return None

    def instructions(self, payment):
        return {'paybill': settings.MPESA_C2B_SHORTCODE, 'accountNumber': payment.reference}


def register_c2b_urls():
    """Registers our validation and confirmation URLs for the Paybill (run once per environment)."""
    _, data = daraja_post('/mpesa/c2b/v2/registerurl', {
        'ShortCode': settings.MPESA_C2B_SHORTCODE,
        # If our validation URL cannot be reached, Safaricom completes the payment; the confirmation
        # still has to match a pending booking and amount before anything is confirmed
        'ResponseType': 'Completed',
        'ConfirmationURL': callback_url('c2b-confirm'),
        'ValidationURL': callback_url('c2b-validate'),
    })
    return data
