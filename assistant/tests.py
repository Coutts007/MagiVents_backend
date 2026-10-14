import json
import logging
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from .providers import AssistantError, AssistantProvider, MockProvider, Usage
from .providers.claude import ClaudeProvider
from .schema import sanitize_form_state, validate_suggestions
from .services import build_messages

URL = '/api/assistant/event/'

FORM = {
    'title': 'Karura Charity 5K', 'tagline': '', 'category': 'Sports & Outdoors', 'date': '2026-11-14',
    'startTime': '07:00', 'endTime': '11:00', 'venueName': 'Karura Forest', 'venueCity': 'Nairobi',
    'isFree': False, 'description': '', 'tags': 'running, charity',
    'tiers': [{'name': 'Regular', 'price': '1500', 'quantity': '300', 'admits': '1', 'perks': 'T-shirt'}],
    'agenda': [], 'hostName': 'Wanjiku',
    # Must never reach the model
    'payoutDetails': {'method': 'mpesa_phone', 'phoneNumber': '0712345678'},
    'payoutForm': {'bankAccountNumber': '0123456789'},
    'imageUrl': 'data:image/png;base64,AAAA',
}


def events_of(response):
    """The (event, data) pairs of a server-sent event stream."""
    body = b''.join(response.streaming_content).decode()
    pairs = []
    for block in body.strip().split('\n\n'):
        lines = dict(line.split(': ', 1) for line in block.split('\n'))
        pairs.append((lines['event'], json.loads(lines['data'])))
    return pairs


class ScriptedProvider(AssistantProvider):
    """Plays back given model answers (raw JSON text), one per call, and records what it was sent."""
    name = 'scripted'

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        for i in range(0, len(answer), 7):
            yield 'text', answer[i:i + 7]
        yield 'done', Usage(input_tokens=1200, output_tokens=300, model='scripted-model')


def answer(reply='Here you go.', suggestions=()):
    return json.dumps({'reply': reply, 'suggestions': list(suggestions)})


def suggestion(field, **values):
    return {'field': field, 'label': values.pop('label', ''), 'text': values.pop('text', ''),
            'items': values.pop('items', []), 'agenda': values.pop('agenda', []), 'tiers': values.pop('tiers', [])}


@override_settings(AI_API_KEY='', AI_PROVIDER='anthropic')
class AssistantTestCase(APITestCase):
    def setUp(self):
        cache.clear()  # The assistant throttle lives in the cache
        self.addCleanup(cache.clear)
        MockProvider.chunk_delay = 0
        self.organizer = User.objects.create_user(username='o@x.com', email='o@x.com', password='pw-Org-12345',
                                                  role='curator')
        self.client.force_authenticate(self.organizer)

    def ask(self, **body):
        return self.client.post(URL, {'messages': [], 'formState': FORM, **body}, format='json')

    def use(self, provider):
        return mock.patch('assistant.services.get_provider', return_value=provider)


class AccessTests(AssistantTestCase):
    def test_signed_out_and_attendees_are_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.ask(action='titles').status_code, 401)
        self.client.force_authenticate(User.objects.create_user(username='p@x.com', email='p@x.com', password='pw-Pat-12345'))
        self.assertEqual(self.ask(action='titles').status_code, 403)

    def test_suspended_organizers_are_refused_and_admins_allowed(self):
        self.organizer.is_suspended = True
        self.organizer.save()
        self.assertEqual(self.ask(action='titles').status_code, 403)
        self.client.force_authenticate(User.objects.create_user(username='a@x.com', email='a@x.com', password='pw-Adm-12345',
                                                                role='admin'))
        self.assertEqual(self.ask(action='titles').status_code, 200)

    def test_throttle_per_user(self):
        from rest_framework.throttling import ScopedRateThrottle
        with mock.patch.object(ScopedRateThrottle, 'THROTTLE_RATES',
                               {**ScopedRateThrottle.THROTTLE_RATES, 'assistant': '2/hour'}):
            self.assertEqual(self.ask(action='titles').status_code, 200)
            self.assertEqual(self.ask(action='tags').status_code, 200)
            self.assertEqual(self.ask(action='tags').status_code, 429)
            # Another organizer has their own allowance
            self.client.force_authenticate(User.objects.create_user(username='b@x.com', email='b@x.com',
                                                                    password='pw-Bee-12345', role='curator'))
            self.assertEqual(self.ask(action='tags').status_code, 200)


class MockProviderTests(AssistantTestCase):
    def test_no_key_gives_labelled_sample_titles_that_stream(self):
        events = events_of(self.ask(action='titles'))
        kinds = [e for e, _ in events]
        self.assertEqual(kinds[0], 'meta')
        self.assertEqual(events[0][1], {'provider': 'mock', 'sample': True})
        self.assertIn('delta', kinds)
        done = events[-1][1]
        self.assertEqual(events[-1][0], 'done')
        streamed = ''.join(d['text'] for e, d in events if e == 'delta')
        self.assertEqual(streamed, done['reply'])
        self.assertIn('sample', done['reply'].lower())
        titles = [s for s in done['suggestions'] if s['field'] == 'title']
        self.assertEqual(len(titles), 5)
        self.assertTrue(all(s['label'].startswith('Sample') for s in titles))
        self.assertTrue(done['sample'])

    def test_every_quick_action_returns_valid_suggestions(self):
        for action, field in [('description', 'description'), ('agenda', 'agenda'), ('tagline', 'tagline'),
                              ('tags', 'tags'), ('tiers', 'tiers'), ('attendeeNote', 'attendeeNote')]:
            done = events_of(self.ask(action=action))[-1][1]
            self.assertTrue(done['suggestions'], action)
            self.assertTrue(all(s['field'] == field for s in done['suggestions']), action)
        agenda = events_of(self.ask(action='agenda'))[-1][1]['suggestions'][0]['value']
        self.assertTrue(all('07:00' <= item['time'] <= '11:00' for item in agenda))

    @override_settings(AI_API_KEY='sk-test', AI_PROVIDER='mock')
    def test_ai_provider_mock_wins_over_a_key(self):
        self.assertEqual(events_of(self.ask(action='tags'))[0][1]['provider'], 'mock')

    def test_chat_needs_a_message(self):
        res = self.ask(action='chat')
        self.assertEqual((res.status_code, res.data['code']), (400, 'empty_message'))


class ValidationTests(AssistantTestCase):
    def test_invalid_suggestions_are_dropped(self):
        provider = ScriptedProvider(answer(suggestions=[
            suggestion('category', text='Culinary & Wine'),                        # not one of the nine
            suggestion('category', text='Sports & Outdoors'),
            suggestion('tiers', tiers=[
                {'name': 'Bad', 'price': -100, 'quantity': 10, 'admits': 1, 'description': '', 'perks': []},
                {'name': 'Half', 'price': 99.5, 'quantity': 10, 'admits': 1, 'description': '', 'perks': []},
                {'name': 'Regular', 'price': 1500, 'quantity': 200, 'admits': 1, 'description': 'Entry', 'perks': []},
            ]),
            suggestion('agenda', agenda=[
                {'time': '25:00', 'title': 'Impossible', 'detail': ''},
                {'time': '7:30', 'title': 'Warm-up', 'detail': ''},
                {'time': '13:00', 'title': 'After the event ends', 'detail': ''},
                {'time': '07:00', 'title': 'Registration', 'detail': ''},
            ]),
            suggestion('tags', items=['#running', 'a' * 40, 'Running', 'charity']),
            suggestion('tiers', tiers=[{'name': 'Free', 'price': 0, 'quantity': 5, 'admits': 1, 'description': '', 'perks': []}]),
            suggestion('title', text=''),
        ]))
        with self.use(provider):
            done = events_of(self.ask(messages=[{'role': 'user', 'content': 'help'}]))[-1][1]
        by_field = {}
        for s in done['suggestions']:
            by_field.setdefault(s['field'], []).append(s['value'])
        self.assertEqual(by_field['category'], ['Sports & Outdoors'])
        self.assertEqual(by_field['tiers'], [[{'name': 'Regular', 'price': 1500, 'quantity': 200, 'admits': 1,
                                               'description': 'Entry', 'perks': []}]])  # a free tier in a paid event is dropped
        self.assertEqual([i['time'] for i in by_field['agenda'][0]], ['07:00', '07:30'])
        self.assertEqual(by_field['tags'], [['running', 'charity']])
        self.assertNotIn('title', by_field)

    def test_free_event_tiers_must_be_free(self):
        tiers = [{'name': 'VIP', 'price': 500, 'quantity': 5, 'admits': 1, 'description': '', 'perks': []},
                 {'name': 'Entry', 'price': 0, 'quantity': 50, 'admits': 2, 'description': '', 'perks': []}]
        kept = validate_suggestions([suggestion('tiers', tiers=tiers)], {'isFree': True})
        self.assertEqual([t['name'] for t in kept[0]['value']], ['Entry'])


class ProviderFailureTests(AssistantTestCase):
    def test_malformed_answer_is_retried_once_then_a_clean_error(self):
        provider = ScriptedProvider('{"reply": "Half an answ', 'not json at all')
        with self.use(provider):
            res = self.ask(action='titles')
            self.assertEqual(res.status_code, 200)
            events = events_of(res)
        self.assertEqual(len(provider.requests), 2)
        self.assertIn('reset', [e for e, _ in events])
        self.assertEqual(events[-1], ('error', {'error': 'The assistant gave an answer that could not be read. Please try again.',
                                                'code': 'bad_output', 'retryable': True}))

    def test_retry_recovers(self):
        provider = ScriptedProvider('{"reply": 12}', answer('Fixed', [suggestion('title', text='Karura Run')]))
        with self.use(provider):
            done = events_of(self.ask(action='titles'))[-1]
        self.assertEqual(done[0], 'done')
        self.assertEqual(done[1]['suggestions'], [{'field': 'title', 'value': 'Karura Run'}])

    def test_provider_errors_before_streaming_are_json(self):
        for error, status in [(AssistantError('Busy', 'provider_rate_limited', 503), 503),
                              (AssistantError('Bad key', 'provider_auth', 503, retryable=False), 503)]:
            with self.use(ScriptedProvider(error)):
                res = self.ask(action='titles')
            self.assertEqual((res.status_code, res.data['code']), (status, error.code))
            self.assertIn('retryable', res.data)

    def test_claude_errors_map_to_clear_messages(self):
        import anthropic
        import httpx2
        request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
        cases = [
            (anthropic.AuthenticationError('bad key', response=httpx2.Response(401, request=request), body=None), 'provider_auth'),
            (anthropic.RateLimitError('slow down', response=httpx2.Response(429, request=request), body=None), 'provider_rate_limited'),
            (anthropic.InternalServerError('down', response=httpx2.Response(500, request=request), body=None), 'provider_unavailable'),
            (anthropic.APITimeoutError(request=request), 'timeout'),
            (anthropic.APIConnectionError(request=request), 'provider_unavailable'),
        ]
        for error, code in cases:
            provider = ClaudeProvider('sk-test', 'claude-opus-5-5', 30)
            with mock.patch.object(provider.client.beta.messages, 'stream', side_effect=error):
                with self.assertRaises(AssistantError) as caught:
                    list(provider.stream(mock.Mock(system='s', messages=[], schema={})))
            self.assertEqual(caught.exception.code, code)

    def test_claude_request_uses_structured_output_and_streams(self):
        provider = ClaudeProvider('sk-test', 'claude-opus-5-5', 30)
        text = answer('Hi', [])
        events = [mock.Mock(type='content_block_delta', delta=mock.Mock(type='text_delta', text=text[i:i + 5]))
                  for i in range(0, len(text), 5)]
        final = mock.Mock(stop_reason='end_turn', model='claude-opus-5-5',
                          usage=mock.Mock(input_tokens=10, output_tokens=20))
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.__iter__.return_value = iter(events)
        stream.get_final_message.return_value = final
        with mock.patch.object(provider.client.beta.messages, 'stream', return_value=stream) as call:
            out = list(provider.stream(mock.Mock(system='SYSTEM', messages=[{'role': 'user', 'content': 'x'}],
                                                 schema={'type': 'object'})))
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs['model'], 'claude-opus-5-5')
        self.assertEqual(kwargs['output_config']['format'], {'type': 'json_schema', 'schema': {'type': 'object'}})
        self.assertEqual((kwargs['fallbacks'], kwargs['betas']), ('default', ['server-side-fallback-2026-07-01']))
        self.assertEqual(kwargs['system'][0]['text'], 'SYSTEM')
        self.assertEqual(''.join(p for k, p in out if k == 'text'), text)
        self.assertEqual(out[-1][1].output_tokens, 20)

        final.stop_reason = 'refusal'
        stream.__iter__.return_value = iter([])
        with mock.patch.object(provider.client.beta.messages, 'stream', return_value=stream):
            with self.assertRaises(AssistantError) as caught:
                list(provider.stream(mock.Mock(system='s', messages=[], schema={})))
        self.assertEqual(caught.exception.code, 'refused')


class PromptTests(AssistantTestCase):
    def test_form_state_reaches_the_prompt_without_sensitive_fields(self):
        provider = ScriptedProvider(answer())
        history = [{'role': 'assistant', 'content': 'Hello! (a greeting from the panel)'},
                   {'role': 'user', 'content': 'A charity 5K at Karura'},
                   {'role': 'assistant', 'content': 'Nice idea.'},
                   {'role': 'user', 'content': 'Write the description'}]
        with self.use(provider):
            self.ask(messages=history, action='description')
        request = provider.requests[0]
        self.assertEqual(request.messages[0], {'role': 'user', 'content': 'A charity 5K at Karura'})  # leading assistant dropped
        last = request.messages[-1]
        self.assertEqual(last['role'], 'user')
        form_text, user_text, task = (b['text'] for b in last['content'])
        self.assertIn('<event_form>', form_text)
        self.assertIn('"title": "Karura Charity 5K"', form_text)
        self.assertIn('"startTime": "07:00"', form_text)
        self.assertEqual(user_text, 'Write the description')
        self.assertEqual(task, 'Write the event description.')
        for secret in ('0712345678', '0123456789', 'payout', 'base64'):
            self.assertNotIn(secret, json.dumps(request.messages))
        self.assertIn('Kenyan', request.system)

    def test_history_is_capped_and_long_fields_trimmed(self):
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'message {i}'} for i in range(31)]
        messages = build_messages(history, {}, 'chat')
        self.assertLessEqual(len(messages), 10)
        form = sanitize_form_state({'description': 'x' * 10000, 'tags': ['t'] * 50, 'title': 'T' * 999})
        self.assertEqual((len(form['description']), len(form['tags']), len(form['title'])), (3000, 15, 255))

    def test_usage_is_logged_without_content(self):
        provider = ScriptedProvider(answer('A reply with the organizer\'s words: Karura'))
        with self.use(provider), self.assertLogs('assistant.services', logging.INFO) as logs:
            events_of(self.ask(messages=[{'role': 'user', 'content': 'Secret idea about Karura'}]))
        line = next(m for m in logs.output if 'input_tokens' in m)
        self.assertIn(f'user={self.organizer.id}', line)
        self.assertIn('input_tokens=1200', line)
        self.assertNotIn('Karura', '\n'.join(logs.output))
