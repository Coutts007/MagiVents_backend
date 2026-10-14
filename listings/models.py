import secrets

from django.conf import settings
from django.db import models

from gatherings.models import Gathering


class CategoryFee(models.Model):
    """The listing fee, in whole KES, an organizer pays the platform to publish an event in this category.
    0 (the default, also for a category with no row) means no fee."""
    category = models.CharField(max_length=60, choices=Gathering.CATEGORY_CHOICES, unique=True)
    amount = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')

    class Meta:
        ordering = ['category']

    def __str__(self):
        return f'{self.category}: KSh {self.amount}'

    @classmethod
    def fee_for(cls, category):
        return cls.objects.filter(category=category).values_list('amount', flat=True).first() or 0


class PlatformPaymentAccount(models.Model):
    """Where organizers pay listing fees: the platform's Paybill or Till, shown with instructions.
    One row (pk=1), edited by admins. Display details only; provider credentials stay in the environment."""
    METHOD_CHOICES = (
        ('paybill', 'M-Pesa Paybill'),
        ('till', 'M-Pesa Till (Buy Goods)'),
    )

    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='paybill')
    business_number = models.CharField(max_length=10, blank=True, default='')
    # Paybill account number organizers type. Empty: each event's own listing reference is used.
    account_number = models.CharField(max_length=40, blank=True, default='')
    account_name = models.CharField(max_length=100, blank=True, default='')
    instructions = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')

    def __str__(self):
        return f'{self.get_method_display()} {self.business_number}'

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


def new_listing_reference():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return 'LF' + ''.join(secrets.choice(alphabet) for _ in range(8))


class ListingFee(models.Model):
    """The listing fee of one event: charged once per event, before it is first published.

    `cleared` means the fee has been settled for `cleared_category` (paid, waived, or published while that
    category's fee was 0), at a fee level of `cleared_amount` KES. Unpublishing and republishing never charges
    again. Moving to a category whose fee is higher than `cleared_amount` costs the difference, paid before the
    change takes effect; moving to a cheaper one is free and gives no refund. A later change to a category's
    fee never affects events already cleared.
    """
    STATUS_CHOICES = (
        ('unpaid', 'Unpaid'),
        ('pending', 'Payment pending'),
        ('paid', 'Paid'),
        ('waived', 'Waived'),
        ('exempt', 'No fee'),
    )

    gathering = models.OneToOneField(Gathering, on_delete=models.CASCADE, related_name='listing_fee')
    # Also the account number an organizer uses when paying the platform Paybill by hand
    reference = models.CharField(max_length=12, unique=True, default=new_listing_reference)
    # Kept up to date for filtering; see listings.services.refresh_status
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='unpaid', db_index=True)

    cleared = models.BooleanField(default=False)
    cleared_category = models.CharField(max_length=60, blank=True, default='')
    cleared_amount = models.PositiveIntegerField(default=0)
    # Money actually received (KES), and the reference of the payment that cleared the fee
    amount_paid = models.PositiveIntegerField(default=0)
    amount_waived = models.PositiveIntegerField(default=0)
    payment_reference = models.CharField(max_length=120, blank=True, default='')
    paid_at = models.DateTimeField(null=True, blank=True)
    # Events that existed before listing fees (and the demo events) count as paid
    grandfathered = models.BooleanField(default=False)
    # The organizer asked to publish: the event goes live as soon as the fee is confirmed
    publish_requested = models.BooleanField(default=False)

    waived_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='+')
    waived_at = models.DateTimeField(null=True, blank=True)
    waive_reason = models.TextField(blank=True, default='')
    marked_paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    marked_paid_at = models.DateTimeField(null=True, blank=True)
    mark_paid_reason = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Listing fee {self.reference} ({self.status})'
