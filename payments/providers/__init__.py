"""Provider registry. PAYMENTS_MODE=simulated (default) uses the fake gateway for every method;
PAYMENTS_MODE=live uses Daraja for M-Pesa and Paystack for cards."""
from django.conf import settings

from ..services import PaymentError
from .base import PaymentProvider, QueryResult
from .simulated import SimulatedProvider


def live_mode():
    return settings.PAYMENTS_MODE == 'live'


def get_provider(method, mpesa_mode='stk'):
    if method not in ('mpesa', 'card'):
        raise PaymentError('Pay with M-Pesa or card.')
    if method == 'mpesa' and mpesa_mode not in ('stk', 'paybill'):
        raise PaymentError('Choose STK Push or Paybill.')
    if not live_mode():
        return SimulatedProvider(method, mpesa_mode)
    if method == 'card':
        from .paystack import PaystackProvider
        return PaystackProvider()
    from .daraja import DarajaPaybillProvider, DarajaStkProvider
    return DarajaStkProvider() if mpesa_mode == 'stk' else DarajaPaybillProvider()


def provider_for_payment(payment):
    if payment.provider in ('simulated', 'manual'):
        mode = (payment.booking.mpesa_mode if payment.booking_id else '') or 'stk'
        return SimulatedProvider(payment.method, mode)
    if payment.provider == 'paystack':
        from .paystack import PaystackProvider
        return PaystackProvider()
    from .daraja import DarajaPaybillProvider, DarajaStkProvider
    return DarajaStkProvider() if payment.provider == 'mpesa_stk' else DarajaPaybillProvider()


def available_methods():
    """Which checkout options work with the current configuration (shown by the frontend)."""
    return {
        'mpesaStk': get_provider('mpesa', 'stk').is_configured(),
        'mpesaPaybill': get_provider('mpesa', 'paybill').is_configured(),
        'card': get_provider('card').is_configured(),
    }


__all__ = ['PaymentProvider', 'QueryResult', 'SimulatedProvider', 'get_provider', 'provider_for_payment', 'available_methods', 'live_mode']
