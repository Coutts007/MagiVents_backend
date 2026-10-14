"""
AUTOMATED PAYOUTS: NOT BUILT YET. This is the place to add them.

Today an admin sends each organizer their money by hand (M-Pesa, bank transfer) and then records it in the
admin dashboard, which creates a payouts.models.Payout through payouts.services.record_payout().

An automated provider would implement the interface below and be returned by get_automated_payout_provider():

* Daraja B2C (Business to Customer): the platform's shortcode sends M-Pesa to an organizer's phone number.
  Needs B2C credentials (initiator name and security credential), a result and timeout callback URL, and
  idempotent handling of the result callback, like payments/hooks.py does for STK callbacks. Till and
  Paybill destinations would need Daraja B2B instead.
* Card-gateway split payments (e.g. Paystack subaccounts): the gateway splits each ticket payment at checkout
  and settles the organizer's share to their bank account. Payouts would then be recorded from the gateway's
  settlement reports rather than sent by us.

Whichever is used, record_payout() must stay the single place that checks the amount against the balance
outstanding under a row lock, so an automated payout can never exceed what the organizer is owed.
"""


class AutomatedPayoutProvider:
    """Sends money to an organizer's payment details. Not implemented by any provider yet."""
    name = ''

    def can_send_to(self, payout_account):
        """True when this provider can pay out to these details (e.g. B2C only to M-Pesa numbers)."""
        raise NotImplementedError

    def send(self, payout_account, amount, reference):
        """Starts the transfer and returns the provider's reference; the result arrives by callback."""
        raise NotImplementedError


def get_automated_payout_provider():
    """None: payouts are made by hand and recorded by an admin."""
    return None
