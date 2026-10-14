from django.test import override_settings
import json
from pathlib import Path

from django.core.management import call_command
from rest_framework.test import APITestCase

from accounts.models import User
from .models import Gathering


def give_payout_details(user):
    from payouts.models import PayoutAccount
    PayoutAccount.objects.get_or_create(organizer=user, gathering=None,
                                        defaults={'method': 'mpesa_phone', 'phone_number': '254712345678'})

SEED = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'seed_events.json').read_text())


def event_payload(**overrides):
    event = {k: v for k, v in SEED[0].items() if k not in ('id', 'attendeeCount')}
    event.update(overrides)
    return event


class GatheringApiTests(APITestCase):
    def setUp(self):
        # Creating events needs the organizer role (Phase 2); the assertions below are unchanged
        self.owner = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Owner-123',
                                              role='curator')
        # Publishing paid events needs payment details (Part 3); the assertions below are unchanged
        give_payout_details(self.owner)
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

    def test_kes_free_events_categories_and_created_at(self):
        call_command('seed_gatherings', verbosity=0)
        self.client.force_authenticate(self.owner)

        # Paid events need a price above 0
        paid = event_payload(isFree=False)
        paid['pricing'] = {**paid['pricing'], 'startingPrice': 0}
        self.assertEqual(self.client.post('/api/gatherings/', paid, format='json').status_code, 400)

        # Free events have every price forced to 0, in KES
        free = event_payload(isFree=True, agenda=[])
        res = self.client.post('/api/gatherings/', free, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['isFree'])
        self.assertEqual(res.data['pricing']['currency'], 'KES')
        self.assertEqual(res.data['pricing']['startingPrice'], 0)
        self.assertTrue(all(t['price'] == 0 for t in res.data['pricing']['tiers']))
        self.assertEqual(res.data['agenda'], [])
        self.assertTrue(res.data['createdAt'])

        # Old categories are no longer accepted; new ones are searchable
        self.assertEqual(
            self.client.post('/api/gatherings/', event_payload(category='Culinary & Wine'), format='json').status_code, 400
        )
        found = self.client.get('/api/gatherings/', {'search': 'Tech & Innovation'}).data
        self.assertTrue(found and all(e['category'] == 'Tech & Innovation' for e in found))

    def test_organizer_defined_agenda_is_stored_in_order(self):
        self.client.force_authenticate(self.owner)
        agenda = [{'time': '09:00', 'title': 'Opening', 'detail': ''}, {'time': '10:00', 'title': 'Panel', 'detail': 'Q&A'}]
        res = self.client.post('/api/gatherings/', event_payload(agenda=agenda), format='json')
        self.assertEqual(res.data['agenda'], agenda)


# Paid bookings confirm within the request when the simulated gateway has no delay (Phase 4)
@override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0)
class OrganizerStatsTests(APITestCase):
    def setUp(self):
        call_command('seed_gatherings', verbosity=0)
        self.organizer = User.objects.get(email='curator@magivents.local')
        self.event = Gathering.objects.get(id='nairobi-sounds-live')
        self.tier = self.event.tiers.first()

    def test_revenue_comes_from_bookings_and_is_private(self):
        buyer = User.objects.create_user(username='b@x.com', email='b@x.com', password='pw-Buyer-123')
        self.client.force_authenticate(buyer)
        res = self.client.post('/api/bookings/', {
            'eventId': self.event.id, 'tierId': str(self.tier.id), 'quantity': 2,
            'attendeeName': 'B', 'attendeeEmail': 'b@x.com', 'paymentMethod': 'card',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        paid = res.data['totalPrice']

        # Other users never see sales figures
        self.assertIsNone(self.client.get(f'/api/gatherings/{self.event.id}/').data['organizerStats'])

        self.client.force_authenticate(self.organizer)
        stats = self.client.get(f'/api/gatherings/{self.event.id}/').data['organizerStats']
        self.assertEqual(stats, {'revenue': paid, 'ticketsSold': 2})
        listed = next(e for e in self.client.get('/api/gatherings/').data if e['id'] == self.event.id)
        self.assertEqual(listed['organizerStats'], stats)
        empty = Gathering.objects.filter(organizer=self.organizer).exclude(id=self.event.id).first()
        self.assertEqual(self.client.get(f'/api/gatherings/{empty.id}/').data['organizerStats'],
                         {'revenue': 0.0, 'ticketsSold': 0})


# Paid bookings confirm within the request when the simulated gateway has no delay (Phase 4)
@override_settings(PAYMENTS_SIMULATED_DELAY_SECONDS=0)
class TierEditorAndRoleTests(APITestCase):
    def setUp(self):
        self.curator = User.objects.create_user(username='c@x.com', email='c@x.com', password='pw-Curator-1',
                                                role='curator')
        # Publishing paid events needs payment details (Part 3); the assertions below are unchanged
        give_payout_details(self.curator)

    def test_patron_must_become_organizer_to_create(self):
        patron = User.objects.create_user(username='p@x.com', email='p@x.com', password='pw-Patron-123')
        self.client.force_authenticate(patron)
        self.assertEqual(self.client.post('/api/gatherings/', event_payload(), format='json').status_code, 403)
        res = self.client.post('/api/auth/become-organizer/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['role'], 'curator')
        give_payout_details(patron)  # Part 3 fixture: paid events need payment details to publish
        self.assertEqual(self.client.post('/api/gatherings/', event_payload(), format='json').status_code, 201)

    def test_tier_quantities_set_capacity_and_order(self):
        self.client.force_authenticate(self.curator)
        payload = event_payload()
        payload['pricing'] = {'currency': 'KES', 'startingPrice': 999, 'tiers': [
            {'id': '', 'name': 'VIP', 'price': 3000, 'quantity': 20, 'perks': ['Front row']},
            {'id': '', 'name': 'Regular', 'price': 1000, 'quantity': 80},
        ]}
        res = self.client.post('/api/gatherings/', payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['capacity'], 100)
        self.assertEqual(res.data['pricing']['startingPrice'], 1000)
        self.assertEqual([t['name'] for t in res.data['pricing']['tiers']], ['VIP', 'Regular'])
        self.assertEqual(res.data['pricing']['tiers'][0]['available'], 20)

        # Book 5 VIP tickets, then the quantity cannot drop below what is sold
        gid, vip = res.data['id'], res.data['pricing']['tiers'][0]
        # New events are saved as drafts (listing fee brief): publish it before booking
        self.assertEqual(res.data['status'], 'draft')
        self.assertEqual(self.client.patch(f'/api/gatherings/{gid}/', {'status': 'published'},
                                           format='json').status_code, 200)
        booking = self.client.post('/api/bookings/', {
            'eventId': gid, 'tierId': vip['id'], 'quantity': 5, 'attendeeName': 'A', 'attendeeEmail': 'a@x.com',
            'paymentMethod': 'card'}, format='json')
        self.assertEqual(booking.status_code, 201, booking.data)

        event = self.client.get(f'/api/gatherings/{gid}/').data
        tiers = event['pricing']['tiers']
        self.assertEqual((tiers[0]['sold'], tiers[0]['available']), (5, 15))
        tiers[0]['quantity'] = 4
        self.assertEqual(self.client.put(f'/api/gatherings/{gid}/', event, format='json').status_code, 400)

        # Grow VIP to 30 and drop Regular, which has no sales
        tiers[0]['quantity'] = 30
        event['pricing']['tiers'] = [tiers[0]]
        res = self.client.put(f'/api/gatherings/{gid}/', event, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['capacity'], 30)
        self.assertEqual(res.data['pricing']['tiers'][0]['available'], 25)

        # A tier with sales cannot be removed
        event = res.data
        event['pricing']['tiers'] = [{'id': '', 'name': 'Other', 'price': 500, 'quantity': 10}]
        self.assertEqual(self.client.put(f'/api/gatherings/{gid}/', event, format='json').status_code, 400)

    def test_paid_event_tiers_need_a_price(self):
        self.client.force_authenticate(self.curator)
        payload = event_payload()
        # Replace (not mutate) the nested pricing, which is shared with the SEED fixture
        payload['pricing'] = {**payload['pricing'], 'tiers': [{'id': '', 'name': 'Oops', 'price': 0, 'quantity': 10}]}
        self.assertEqual(self.client.post('/api/gatherings/', payload, format='json').status_code, 400)


class SuspendedOrganizerTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username='s@x.com', email='s@x.com', password='pw-Susp-123',
                                                  role='curator')
        self.client.force_authenticate(self.organizer)
        self.draft_id = self.client.post('/api/gatherings/', event_payload(status='draft'), format='json').data['id']
        self.organizer.is_suspended = True
        self.organizer.save()

    def test_cannot_create(self):
        res = self.client.post('/api/gatherings/', event_payload(), format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'organizer_suspended')

    def test_cannot_publish_but_still_sees_own_events(self):
        res = self.client.patch(f'/api/gatherings/{self.draft_id}/', {'status': 'published'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Gathering.objects.get(id=self.draft_id).status, 'draft')
        event = self.client.get(f'/api/gatherings/{self.draft_id}/').data
        event['status'] = 'published'
        self.assertEqual(self.client.put(f'/api/gatherings/{self.draft_id}/', event, format='json').status_code, 403)
        mine = self.client.get('/api/gatherings/my_gatherings/')
        self.assertEqual([e['id'] for e in mine.data], [self.draft_id])
        # Editing a draft is still allowed
        self.assertEqual(self.client.patch(f'/api/gatherings/{self.draft_id}/', {'title': 'Renamed'},
                                           format='json').status_code, 200)
