import re


def normalize_kenyan_phone(value):
    """'0712 345 678', '+254712345678', '712345678' -> '254712345678'. Raises ValueError if it is not a
    Kenyan mobile number (Safaricom numbers start 07xx or 01xx)."""
    digits = re.sub(r'[\s\-+()]', '', value or '')
    if not digits.isdigit():
        raise ValueError('Enter a valid M-Pesa number, e.g. 0712 345 678.')
    if digits.startswith('254'):
        digits = digits[3:]
    elif digits.startswith('0'):
        digits = digits[1:]
    if len(digits) != 9 or digits[0] not in '71':
        raise ValueError('Enter a valid M-Pesa number, e.g. 0712 345 678.')
    return '254' + digits
