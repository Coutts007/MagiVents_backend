from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class QueryResult:
    """What a provider says about a payment when asked (rather than told by a callback)."""
    status: str  # 'pending' | 'succeeded' | 'failed'
    amount: Decimal = Decimal('0')
    receipt: str = ''
    provider_reference: str = ''
    reason: str = ''
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    """The small interface every gateway implements. Add a gateway by subclassing and registering it."""
    name = ''
    method = ''  # 'mpesa' or 'card'
    #: Minimum seconds between status queries for one payment
    query_interval = 10
    #: Seconds to wait after starting before the first status query
    min_query_age = 0
    not_configured_message = 'This payment method is not available right now.'

    def is_configured(self):
        return True

    def initiate(self, payment):
        """Starts the payment with the provider (sets provider_reference / authorization_url).
        Raises PaymentError with an attendee-safe message when the provider refuses."""
        raise NotImplementedError

    def query(self, payment):
        """Returns a QueryResult, or None when the provider has no way to be asked."""
        return None

    def instructions(self, payment):
        """Extra, provider-specific data the checkout screen shows while the payment is pending."""
        return {}
