from django.db import models

# Create your models here.
import uuid
from django.conf import settings
from gatherings.models import Gathering, TicketTier

class Booking(models.Model):
    PAYMENT_CHOICES = (
        ('mpesa', 'M-Pesa'),
        ('card', 'Credit / Debit Card'),
        ('complimentary', 'Organizer Invitation'),
        ('free', 'Free Entry'),
    )

    id = models.CharField(primary_key=True, max_length=100, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='bookings')
    gathering = models.ForeignKey(Gathering, on_delete=models.CASCADE, related_name='bookings')
    tier = models.ForeignKey(TicketTier, on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')

    tier_name = models.CharField(max_length=120)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)

    attendee_name = models.CharField(max_length=120)
    attendee_email = models.EmailField()
    ticket_code = models.CharField(max_length=60, unique=True)
    booking_date = models.DateTimeField(auto_now_add=True)

    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='card')
    mpesa_phone_number = models.CharField(max_length=30, blank=True, default='')
    mpesa_receipt_number = models.CharField(max_length=50, blank=True, default='')
    total_in_kes = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    mpesa_mode = models.CharField(max_length=20, blank=True, default='')
    currency = models.CharField(max_length=10, default='KES')
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-booking_date']

    def __str__(self):
        return f"{self.ticket_code} - {self.gathering.title}"


class PromoCode(models.Model):
    """A percentage discount, either site-wide (no gathering) or for one organizer's event."""
    code = models.CharField(max_length=40)
    percent_off = models.PositiveSmallIntegerField()
    gathering = models.ForeignKey(Gathering, on_delete=models.CASCADE, null=True, blank=True, related_name='promo_codes')
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['code', 'gathering'], name='unique_promo_code_per_event'),
            models.UniqueConstraint(fields=['code'], condition=models.Q(gathering__isnull=True),
                                    name='unique_sitewide_promo_code'),
            models.CheckConstraint(condition=models.Q(percent_off__gte=1, percent_off__lte=100),
                                   name='promo_percent_between_1_and_100'),
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} ({self.percent_off}%)'

    def problem(self, gathering, now=None):
        """Why this code cannot be used for `gathering` right now, or None when it can."""
        from django.utils import timezone
        now = now or timezone.now()
        if not self.is_active or (self.gathering_id and self.gathering_id != gathering.id):
            return 'This promo code is not valid.'
        if self.valid_from and now < self.valid_from:
            return 'This promo code is not active yet.'
        if self.valid_until and now > self.valid_until:
            return 'This promo code has expired.'
        if self.max_uses is not None and self.use_count >= self.max_uses:
            return 'This promo code has been fully used.'
        return None

    @classmethod
    def lookup(cls, code, gathering):
        """The event's own code wins over a site-wide code with the same name."""
        code = (code or '').strip().upper()
        if not code:
            return None
        return (cls.objects.filter(code=code, gathering=gathering).first()
                or cls.objects.filter(code=code, gathering__isnull=True).first())


class Bookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookmarks')
    gathering = models.ForeignKey(Gathering, on_delete=models.CASCADE, related_name='bookmarked_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'gathering')