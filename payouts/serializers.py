import re

from rest_framework import serializers

from payments.phone import normalize_kenyan_phone
from .models import PayoutAccount

DIGITS = re.compile(r'^\d+$')


class PayoutAccountSerializer(serializers.Serializer):
    """Reads and writes payment details in camelCase. Each method validates only its own fields."""
    method = serializers.ChoiceField(choices=PayoutAccount.METHOD_CHOICES)
    phoneNumber = serializers.CharField(required=False, allow_blank=True, max_length=20)
    tillNumber = serializers.CharField(required=False, allow_blank=True, max_length=20)
    paybillNumber = serializers.CharField(required=False, allow_blank=True, max_length=20)
    paybillAccount = serializers.CharField(required=False, allow_blank=True, max_length=40)
    bankName = serializers.CharField(required=False, allow_blank=True, max_length=100)
    bankAccountName = serializers.CharField(required=False, allow_blank=True, max_length=100)
    bankAccountNumber = serializers.CharField(required=False, allow_blank=True, max_length=40)

    def validate(self, attrs):
        method = attrs['method']
        clean = lambda key: re.sub(r'[\s-]', '', attrs.get(key, '') or '')  # noqa: E731
        fields = {'method': method}
        if method == 'mpesa_phone':
            try:
                fields['phone_number'] = normalize_kenyan_phone(attrs.get('phoneNumber', ''))
            except ValueError as exc:
                raise serializers.ValidationError({'phoneNumber': str(exc)})
        elif method == 'till':
            till = clean('tillNumber')
            if not DIGITS.match(till) or not 5 <= len(till) <= 7:
                raise serializers.ValidationError({'tillNumber': 'A Till number has 5 to 7 digits, e.g. 5123456.'})
            fields['till_number'] = till
        elif method == 'paybill':
            paybill, account = clean('paybillNumber'), (attrs.get('paybillAccount') or '').strip()
            if not DIGITS.match(paybill) or not 5 <= len(paybill) <= 7:
                raise serializers.ValidationError({'paybillNumber': 'A Paybill (business) number has 5 to 7 digits, e.g. 247247.'})
            if not re.match(r'^[A-Za-z0-9][A-Za-z0-9 \-]{0,19}$', account):
                raise serializers.ValidationError(
                    {'paybillAccount': 'Enter the account number for the Paybill (up to 20 letters or digits).'})
            fields.update(paybill_number=paybill, paybill_account=account.upper())
        else:
            bank, name, number = (attrs.get('bankName') or '').strip(), (attrs.get('bankAccountName') or '').strip(), clean('bankAccountNumber')
            if len(bank) < 2:
                raise serializers.ValidationError({'bankName': 'Enter the name of the bank, e.g. Equity Bank.'})
            if len(name) < 2:
                raise serializers.ValidationError({'bankAccountName': 'Enter the name the bank account is held in.'})
            if not DIGITS.match(number) or not 6 <= len(number) <= 20:
                raise serializers.ValidationError({'bankAccountNumber': 'A bank account number has 6 to 20 digits.'})
            fields.update(bank_name=bank, bank_account_name=name, bank_account_number=number)
        return fields


def payout_account_payload(account):
    if account is None:
        return None
    local_phone = '0' + account.phone_number[3:] if account.phone_number else ''
    return {
        'method': account.method,
        'methodLabel': account.get_method_display(),
        'phoneNumber': local_phone,
        'tillNumber': account.till_number,
        'paybillNumber': account.paybill_number,
        'paybillAccount': account.paybill_account,
        'bankName': account.bank_name,
        'bankAccountName': account.bank_account_name,
        'bankAccountNumber': account.bank_account_number,
        'summary': account.summary(),
        'scope': 'event' if account.gathering_id else 'default',
        'updatedAt': account.updated_at.isoformat() if account.updated_at else None,
    }


EMPTY_FIELDS = {'phone_number': '', 'till_number': '', 'paybill_number': '', 'paybill_account': '',
                'bank_name': '', 'bank_account_name': '', 'bank_account_number': ''}


def save_payout_account(organizer, validated, *, gathering=None, by=None):
    """Creates or replaces the organizer's default details (or one event's override)."""
    account = PayoutAccount.objects.filter(organizer=organizer, gathering=gathering).first() \
        or PayoutAccount(organizer=organizer, gathering=gathering)
    for field, value in {**EMPTY_FIELDS, **validated}.items():
        setattr(account, field, value)
    account.updated_by = by
    account.save()
    return account
