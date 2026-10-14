from django.conf import settings
from django.db import models
from django.db.models import Q


class PayoutAccount(models.Model):
    """Where MagiVents pays out an organizer's ticket money.

    The platform collects every ticket payment (an M-Pesa STK Push pays into the account whose credentials the
    platform holds) and then pays organizers out to these details. One account per organizer is their default
    (no gathering); an account with a gathering overrides the default for that one event.

    Only the organizer and admins can read these details; no public endpoint ever returns them.
    """
    METHOD_CHOICES = (
        ('mpesa_phone', 'M-Pesa phone number'),
        ('till', 'M-Pesa Till number (Buy Goods)'),
        ('paybill', 'M-Pesa Paybill'),
        ('bank', 'Bank account'),
    )

    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payout_accounts')
    gathering = models.OneToOneField('gatherings.Gathering', on_delete=models.CASCADE, null=True, blank=True,
                                     related_name='payout_account')
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    # M-Pesa phone, stored as 2547XXXXXXXX / 2541XXXXXXXX
    phone_number = models.CharField(max_length=12, blank=True, default='')
    till_number = models.CharField(max_length=10, blank=True, default='')
    paybill_number = models.CharField(max_length=10, blank=True, default='')
    paybill_account = models.CharField(max_length=40, blank=True, default='')
    bank_name = models.CharField(max_length=100, blank=True, default='')
    bank_account_name = models.CharField(max_length=100, blank=True, default='')
    bank_account_number = models.CharField(max_length=30, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organizer'], condition=Q(gathering__isnull=True),
                                    name='one_default_payout_account_per_organizer'),
        ]

    def __str__(self):
        scope = f'event {self.gathering_id}' if self.gathering_id else 'default'
        return f'{self.get_method_display()} for {self.organizer} ({scope})'

    def summary(self):
        """One line for lists, e.g. "M-Pesa 0712 345 678" or "Paybill 247247, account 0012345"."""
        if self.method == 'mpesa_phone':
            local = '0' + self.phone_number[3:]
            return f'M-Pesa {local[:4]} {local[4:7]} {local[7:]}'
        if self.method == 'till':
            return f'Till {self.till_number}'
        if self.method == 'paybill':
            return f'Paybill {self.paybill_number}, account {self.paybill_account}'
        return f'{self.bank_name}, {self.bank_account_name}, {self.bank_account_number}'


def payout_account_for(gathering):
    """The details the event's ticket money is paid out to: the event's own, else the organizer's default."""
    override = PayoutAccount.objects.filter(gathering=gathering).first()
    if override is not None:
        return override
    return PayoutAccount.objects.filter(organizer_id=gathering.organizer_id, gathering__isnull=True).first()
