import uuid

from django.db import models


class Payment(models.Model):
    """One attempt to pay for a booking with one provider. A booking can have several (retries)."""
    PROVIDER_CHOICES = (
        ('simulated', 'Simulated'),
        ('mpesa_stk', 'M-Pesa STK Push'),
        ('mpesa_paybill', 'M-Pesa Paybill'),
        ('paystack', 'Card (Paystack)'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('succeeded', 'Succeeded'),
        ('failed', 'Failed'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey('tickets.Booking', on_delete=models.CASCADE, related_name='payments')
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    # What the attendee picked: mpesa (stk / paybill) or card
    method = models.CharField(max_length=20)
    # Our reference: Paystack transaction reference, or the Paybill account number
    reference = models.CharField(max_length=60, unique=True)
    # The provider's id: STK CheckoutRequestID, C2B TransID, Paystack transaction id
    provider_reference = models.CharField(max_length=120, blank=True, default='', db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='KES')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    phone_number = models.CharField(max_length=20, blank=True, default='')
    receipt_number = models.CharField(max_length=60, blank=True, default='')
    authorization_url = models.URLField(max_length=500, blank=True, default='')
    failure_reason = models.CharField(max_length=255, blank=True, default='')
    # Paid after the booking had already been confirmed or released: the owner must refund it
    needs_refund = models.BooleanField(default=False)
    # Every callback / query result received, newest last
    raw_payloads = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_queried_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_provider_display()} {self.reference} ({self.status})'

    def log_payload(self, source, payload):
        self.raw_payloads = [*self.raw_payloads, {'source': source, 'payload': payload}][-20:]
