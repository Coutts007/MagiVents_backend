from django.conf import settings
from django.db import models


class Notification(models.Model):
    """An in-app message for one user (today: admins), shown under the bell in the navbar."""
    KIND_CHOICES = (
        ('listing_fee_due', 'Listing fee due'),
        ('listing_fee_paid', 'Listing fee paid'),
        ('listing_fee_failed', 'Listing fee payment failed'),
    )

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=40, choices=KIND_CHOICES)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default='')
    gathering = models.ForeignKey('gatherings.Gathering', on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+')
    # What the notification is about (e.g. "listing_fee_paid:<payment id>"): one notification per recipient,
    # however many times the same event is reported
    dedupe_key = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['recipient', 'dedupe_key'], name='one_notification_per_event_and_recipient'),
        ]

    def __str__(self):
        return f'{self.title} -> {self.recipient}'
