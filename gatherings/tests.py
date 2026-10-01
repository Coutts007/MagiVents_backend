import json
from pathlib import Path

from django.core.management import call_command
from rest_framework.test import APITestCase

from accounts.models import User
from .models import Gathering

SEED = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'seed_events.json').read_text())


def event_payload(**overrides):
    event = {k: v for k, v in SEED[0].items() if k not in ('id', 'attendeeCount')}
    event.update(overrides)
    return event


class GatheringApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Owner-123')
        self.other = User.objects.create_user(username='p@x.com', email='p@x.com', password='pw-Other-123')

    def test_seed_and_public_list(self):
        call_command('seed_gatherings', verbosity=0)
        res = self.client.get('/api/gatherings/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), len(SEED))
        first = next(e for e in res.data if e['id'] == SEED[0]['id'])
        self.assertEqual(first['pricing']['startingPrice'], SEED[0]['pricing']['startingPrice'])
        self.assertIsInstance(first['pricing']['tiers'][0]['price'], float)
        self.assertEqual(first['agenda'], SEED[0]['agenda'])

    def test_create_requires_auth(self):
        self.assertEqual(self.client.post('/api/gatherings/', event_payload(), format='json').status_code, 401)

    def test_owner_crud_and_permissions(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post('/api/gatherings/', event_payload(status='draft'), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        gid = res.data['id']
        self.assertEqual(res.data['organizerId'], str(self.owner.id))
        self.assertEqual(len(res.data['pricing']['tiers']), 2)

        # Drafts are hidden from everyone else
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/gatherings/{gid}/').status_code, 404)

        # Another user can see it once published, but cannot change it
        self.client.force_authenticate(self.owner)
        res = self.client.patch(f'/api/gatherings/{gid}/', {'status': 'published'}, format='json')
        self.assertEqual(res.data['status'], 'published')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(f'/api/gatherings/{gid}/', {'title': 'Hijack'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/gatherings/{gid}/').status_code, 403)

        # Full update from the editor keeps tier ids it sends and drops the rest
        self.client.force_authenticate(self.owner)
        event = self.client.get(f'/api/gatherings/{gid}/').data
        kept = event['pricing']['tiers'][0]
        event['pricing']['tiers'] = [kept, {'id': 'tier-new-1', 'name': 'New', 'price': 10, 'available': 5, 'perks': []}]
        event['title'] = 'Renamed'
        res = self.client.put(f'/api/gatherings/{gid}/', event, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['title'], 'Renamed')
        self.assertEqual(res.data['pricing']['tiers'][0]['id'], kept['id'])
        self.assertEqual([t['name'] for t in res.data['pricing']['tiers']], [kept['name'], 'New'])

        self.assertEqual(self.client.delete(f'/api/gatherings/{gid}/').status_code, 204)
        self.assertFalse(Gathering.objects.exists())
