"""
Sample suggestions without an AI key, so the assistant can be demoed and tested. Every answer says it is a
sample; the suggestions are filled in from what the organizer already typed, and follow the same schema as a
real model's answer.
"""
import asyncio
import json
import re

from .base import AssistantProvider, Usage

CATEGORY_TAGS = {
    'Sports & Outdoors': ['sports', 'outdoors', 'fitness'],
    'Entertainment & Music': ['live music', 'entertainment', 'nightlife'],
    'Business & Entrepreneurship': ['business', 'networking', 'entrepreneurs'],
    'Tech & Innovation': ['tech', 'innovation', 'developers'],
    'Education & Career': ['education', 'careers', 'learning'],
    'Arts & Culture': ['arts', 'culture', 'creative'],
    'Social Impact & Community': ['community', 'volunteering', 'social impact'],
    'Political': ['civic', 'public forum', 'governance'],
    'Others': ['events', 'community'],
}


def _suggestion(field, label='', text='', items=None, agenda=None, tiers=None):
    return {'field': field, 'label': label, 'text': text, 'items': items or [], 'agenda': agenda or [], 'tiers': tiers or []}


def _minutes(hhmm, default):
    try:
        hours, minutes = hhmm.split(':')
        return int(hours) * 60 + int(minutes)
    except (AttributeError, ValueError):
        return default


class MockProvider(AssistantProvider):
    name = 'mock'
    is_sample = True
    #: Seconds between chunks, so the reply visibly streams in a demo (0 in tests)
    chunk_delay = 0.02
    chunk_size = 24

    async def stream(self, request):
        answer = self.answer(request.action, request.form, request.idea)
        text = json.dumps(answer, ensure_ascii=False)
        for start in range(0, len(text), self.chunk_size):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            yield 'text', text[start:start + self.chunk_size]
        yield 'done', Usage(model='mock')

    # --- canned answers ----------------------------------------------------------------------------

    def answer(self, action, form, idea):
        subject = form.get('title') or self._idea_phrase(idea) or form.get('category') or 'your event'
        builders = {
            'titles': self.titles, 'description': self.description, 'agenda': self.agenda, 'tagline': self.tagline,
            'tags': self.tags, 'tiers': self.tiers, 'attendeeNote': self.attendee_note,
        }
        if action in builders:
            suggestions = builders[action](subject, form)
        elif not (form.get('title') or idea):
            return {'reply': 'Sample mode: tell me about your event in one line (what, who it is for, where) and '
                             'I will suggest titles and a description.', 'suggestions': []}
        else:
            suggestions = self.titles(subject, form) + self.description(subject, form)
        return {
            'reply': 'These are sample suggestions: no AI key is set on the server, so they are filled in from '
                     'your form rather than written for you. Edit them freely.',
            'suggestions': suggestions,
        }

    @staticmethod
    def _idea_phrase(idea):
        # The first clause, at most 8 words, without a dangling "in", "for", "at"...
        words = re.split(r'[,.;:!?]', (idea or '').strip())[0].split()[:8]
        while words and words[-1].lower() in {'a', 'an', 'the', 'in', 'at', 'on', 'for', 'of', 'to', 'and', 'with'}:
            words.pop()
        phrase = ' '.join(words)
        return phrase[:1].upper() + phrase[1:]

    def titles(self, subject, form):
        city = form.get('venueCity') or 'Nairobi'
        base = subject[:50]
        options = [
            ('Sample · Clear', f'{base} in {city}'),
            ('Sample · Playful', f'{base}: Come Through!'),
            ('Sample · Formal', f'The {city} {base}'),
            ('Sample · Bold', f'{base}. Be There.'),
            ('Sample · Short', base.split(':')[0][:30]),
        ]
        return [_suggestion('title', label, text) for label, text in options]

    def description(self, subject, form):
        city = form.get('venueCity') or '[town]'
        venue = form.get('venueName') or '[venue]'
        when = form.get('date') or '[date]'
        text = (
            f'{subject} brings people together at {venue}, {city} on {when}. [Say in one sentence what happens '
            f'at the event.]\n\n'
            f'It is for [who should come]. Expect [two or three highlights], with time to meet others who share '
            f'the same interest.\n\n'
            'Book your place on MagiVents and keep your ticket on your phone for entry.'
        )
        return [_suggestion('description', 'Sample', text)]

    def agenda(self, subject, form):
        start = _minutes(form.get('startTime'), 9 * 60)
        end = _minutes(form.get('endTime'), start + 4 * 60)
        if end <= start:
            end = start + 4 * 60
        titles = [('Arrival and registration', 'Check in with your ticket QR code'),
                  ('Welcome', '[Host name] opens the programme'),
                  ('Main session', '[What happens in the main part]'),
                  ('Break', 'Refreshments'),
                  ('Closing', 'Thanks and next steps')]
        step = (end - start) // len(titles)
        items = [{'time': f'{(start + i * step) // 60 % 24:02d}:{(start + i * step) % 60:02d}', 'title': title,
                  'detail': detail} for i, (title, detail) in enumerate(titles)]
        return [_suggestion('agenda', 'Sample agenda', agenda=items)]

    def tagline(self, subject, form):
        return [_suggestion('tagline', 'Sample', f'{subject}: [one line on why it is worth coming]'),
                _suggestion('tagline', 'Sample', f'Join us for {subject.lower()}')]

    def tags(self, subject, form):
        tags = CATEGORY_TAGS.get(form.get('category'), ['events']) + [(form.get('venueCity') or 'nairobi').lower()]
        return [_suggestion('tags', 'Sample', items=tags)]

    def tiers(self, subject, form):
        if form.get('isFree'):
            tiers = [{'name': 'Free Entry', 'price': 0, 'quantity': 200, 'admits': 1,
                      'description': 'Free registration', 'perks': []}]
        else:
            tiers = [
                {'name': 'Early Bird', 'price': 800, 'quantity': 50, 'admits': 1, 'description': 'For people who book early', 'perks': []},
                {'name': 'Regular', 'price': 1500, 'quantity': 150, 'admits': 1, 'description': 'Standard entry', 'perks': []},
                {'name': 'VIP', 'price': 3500, 'quantity': 30, 'admits': 1, 'description': 'Best seats',
                 'perks': ['Reserved seating', 'Priority entry']},
            ]
        return [_suggestion('tiers', 'Sample starting prices', tiers=tiers)]

    def attendee_note(self, subject, form):
        return [_suggestion('attendeeNote', 'Sample',
                            'Please arrive 15 minutes early and have your ticket QR code ready at the entrance. '
                            '[Add anything attendees should bring.]')]
