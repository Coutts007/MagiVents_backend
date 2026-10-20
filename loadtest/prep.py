import json
from django.contrib.auth.hashers import make_password
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import User
from gatherings.models import Gathering
from gatherings.models import TicketTier
pw = make_password('LoadTest#2026')
User.objects.bulk_create([User(username=f'lt{i}@load.test', email=f'lt{i}@load.test', first_name=f'Load{i}', password=pw) for i in range(3000)], ignore_conflicts=True)
toks = [str(RefreshToken.for_user(u).access_token) for u in User.objects.filter(email__endswith='@load.test').order_by('id')]
Gathering.objects.update(capacity=10_000_000)
TicketTier.objects.update(quantity=10_000_000, available=10_000_000)
json.dump(toks, open('/tmp/tokens.json','w'))
print(len(toks))
