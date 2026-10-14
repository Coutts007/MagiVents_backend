"""
Provider callbacks. They are public and carry no JWT, so each one is protected by:
- Paystack: the HMAC-SHA512 signature, then a server-side verify call before anything is trusted;
- Daraja (unsigned): an unguessable path secret, an optional source-IP allowlist, and matching the
  CheckoutRequestID / account number and the amount against a stored payment.
All handlers are idempotent; see payments.services.
"""
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Payment
from .providers.daraja import STK_RESULT_MESSAGES
from .providers.paystack import result_from_verify, valid_signature, verify_transaction
from .services import PaymentError, record_failure, record_success

logger = logging.getLogger(__name__)

ACCEPTED = {'ResultCode': '0', 'ResultDesc': 'Accepted'}


def client_ip(request):
    if settings.BEHIND_PROXY:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def daraja_allowed(request, secret):
    """The path secret must match; when DARAJA_ALLOWED_IPS is set the caller must be one of them."""
    if not settings.PAYMENT_CALLBACK_SECRET or not hmac.compare_digest(secret, settings.PAYMENT_CALLBACK_SECRET):
        return False
    allowed = settings.DARAJA_ALLOWED_IPS
    return not allowed or client_ip(request) in allowed


def parse_json(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return None


@csrf_exempt
@require_POST
def stk_callback(request, secret):
    if not daraja_allowed(request, secret):
        return HttpResponse(status=404)
    body = parse_json(request)
    callback = ((body or {}).get('Body') or {}).get('stkCallback') or {}
    checkout_id = callback.get('CheckoutRequestID')
    payment = Payment.objects.filter(provider='mpesa_stk', provider_reference=checkout_id).first() if checkout_id else None
    if payment is None:
        logger.warning('STK callback for unknown CheckoutRequestID %s', checkout_id)
        return JsonResponse(ACCEPTED)

    code = callback.get('ResultCode')
    if str(code) == '0':
        items = {item.get('Name'): item.get('Value') for item in (callback.get('CallbackMetadata') or {}).get('Item', [])}
        record_success(payment.id, items.get('Amount', 0), receipt=str(items.get('MpesaReceiptNumber', '')),
                       provider_reference=checkout_id, payload=body, source='stk_callback')
    else:
        reason = STK_RESULT_MESSAGES.get(int(code) if str(code).isdigit() else -1, '')
        record_failure(payment.id, reason, payload=body, source='stk_callback')
    return JsonResponse(ACCEPTED)


def _paybill_payment(body):
    reference = str(body.get('BillRefNumber', '')).strip().upper()
    return Payment.objects.select_related('booking').filter(provider='mpesa_paybill', reference=reference).first()


def _amount(body):
    try:
        return Decimal(str(body.get('TransAmount', ''))).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None


@csrf_exempt
@require_POST
def c2b_validate(request, secret):
    """Safaricom asks before completing a Paybill payment (only when external validation is enabled)."""
    if not daraja_allowed(request, secret):
        return HttpResponse(status=404)
    body = parse_json(request) or {}
    payment = _paybill_payment(body)
    if payment is None or payment.status != 'pending' or payment.booking.status not in ('pending',):
        return JsonResponse({'ResultCode': 'C2B00012', 'ResultDesc': 'Rejected'})
    if _amount(body) != payment.amount:
        return JsonResponse({'ResultCode': 'C2B00013', 'ResultDesc': 'Rejected'})
    return JsonResponse(ACCEPTED)


@csrf_exempt
@require_POST
def c2b_confirm(request, secret):
    """A completed Paybill payment. Matched on the account number and the exact amount."""
    if not daraja_allowed(request, secret):
        return HttpResponse(status=404)
    body = parse_json(request) or {}
    payment = _paybill_payment(body)
    if payment is None:
        logger.warning('Paybill payment %s with unknown account %r needs manual follow-up',
                       body.get('TransID'), body.get('BillRefNumber'))
        return JsonResponse(ACCEPTED)
    amount = _amount(body)
    record_success(payment.id, amount if amount is not None else 0, receipt=str(body.get('TransID', '')),
                   provider_reference=str(body.get('TransID', '')), payload=body, source='c2b_confirmation')
    return JsonResponse(ACCEPTED)


@csrf_exempt
@require_POST
def paystack_webhook(request):
    if not valid_signature(request.body, request.headers.get('x-paystack-signature', '')):
        return HttpResponse(status=401)
    body = parse_json(request) or {}
    reference = (body.get('data') or {}).get('reference')
    payment = Payment.objects.filter(provider='paystack', reference=reference).first() if reference else None
    if payment is None:
        return HttpResponse(status=200)
    if body.get('event') == 'charge.success':
        # The webhook only tells us to look: the verify call decides
        try:
            result = result_from_verify(payment, verify_transaction(payment.reference))
        except PaymentError:
            return HttpResponse(status=503)  # Paystack retries the webhook later
        if result.status == 'succeeded':
            record_success(payment.id, result.amount, provider_reference=result.provider_reference,
                           payload=result.raw, source='paystack_webhook')
        elif result.status == 'failed':
            record_failure(payment.id, result.reason, payload=result.raw, source='paystack_webhook')
    return HttpResponse(status=200)
