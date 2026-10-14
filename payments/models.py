import uuid

from django.conf import settings
from django.db import models


class BookingPaymentManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(purpose='booking')


class Payment(models.Model):
    """One attempt to pay, with one provider, for either a booking (tickets) or an event's listing fee.
    A booking or a listing fee can have several attempts (retries)."""
    PURPOSE_CHOICES = (
        ('booking', 'Ticket booking'),
        ('listing_fee', 'Listing fee'),
    )
    PROVIDER_CHOICES = (
        ('simulated', 'Simulated'),
        ('mpesa_stk', 'M-Pesa STK Push'),
        ('mpesa_paybill', 'M-Pesa Paybill'),
        ('paystack', 'Card (Paystack)'),
        # Money an admin confirmed by hand (e.g. a listing fee paid to the platform Paybill)
        ('manual', 'Recorded by an admin'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('succeeded', 'Succeeded'),
        ('failed', 'Failed'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default='booking', db_index=True)
    # Exactly one of these is set, matching `purpose`
    booking = models.ForeignKey('tickets.Booking', on_delete=models.CASCADE, null=True, blank=True,
                                related_name='payments')
    # Listing-fee payments are money records: an event whose fee was paid cannot be deleted with them
    listing_fee = models.ForeignKey('listings.ListingFee', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='payments')
    # For a listing-fee payment that moves a paid event to a dearer category: the category it pays for
    target_category = models.CharField(max_length=60, blank=True, default='')
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
    # Set by staff in the console once the money has been returned (or the case closed)
    refund_resolved_at = models.DateTimeField(null=True, blank=True)
    refund_resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                           blank=True, related_name='+')
    refund_note = models.TextField(blank=True, default='')
    # Every callback / query result received, newest last
    raw_payloads = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_queried_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    #: Only ticket payments (what the staff console lists)
    booking_payments = BookingPaymentManager()

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(purpose='booking', booking__isnull=False, listing_fee__isnull=True)
                    | models.Q(purpose='listing_fee', booking__isnull=True, listing_fee__isnull=False)
                ),
                name='payment_is_for_a_booking_or_a_listing_fee',
            ),
        ]

    def __str__(self):
        return f'{self.get_provider_display()} {self.reference} ({self.status})'

    def log_payload(self, source, payload):
        self.raw_payloads = [*self.raw_payloads, {'source': source, 'payload': payload}][-20:]
