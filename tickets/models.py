from django.db import models

# Create your models here.
import uuid
from django.conf import settings
from gatherings.models import Gathering

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


class Bookmark(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookmarks')
    gathering = models.ForeignKey(Gathering, on_delete=models.CASCADE, related_name='bookmarked_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'gathering')