"""
What goes to the model (a sanitised copy of the event form) and what comes back (suggestions, validated
against the same rules as the event form before anything reaches the browser).
"""
import re

from gatherings.models import Gathering, TicketTier

FIELDS = ['title', 'tagline', 'description', 'category', 'agenda', 'tags', 'tiers', 'attendeeNote']

# The JSON schema the provider must follow (Anthropic: output_config.format). Every value property is required,
# so the shape is the same for every field; the ones a field does not use are left empty.
OUTPUT_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['reply', 'suggestions'],
    'properties': {
        'reply': {'type': 'string'},
        'suggestions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['field', 'label', 'text', 'items', 'agenda', 'tiers'],
                'properties': {
                    'field': {'type': 'string', 'enum': FIELDS},
                    'label': {'type': 'string'},
                    'text': {'type': 'string'},
                    'items': {'type': 'array', 'items': {'type': 'string'}},
                    'agenda': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'additionalProperties': False,
                            'required': ['time', 'title', 'detail'],
                            'properties': {
                                'time': {'type': 'string'},
                                'title': {'type': 'string'},
                                'detail': {'type': 'string'},
                            },
                        },
                    },
                    'tiers': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'additionalProperties': False,
                            'required': ['name', 'price', 'quantity', 'admits', 'description', 'perks'],
                            'properties': {
                                'name': {'type': 'string'},
                                'price': {'type': 'integer'},
                                'quantity': {'type': 'integer'},
                                'admits': {'type': 'integer'},
                                'description': {'type': 'string'},
                                'perks': {'type': 'array', 'items': {'type': 'string'}},
                            },
                        },
                    },
                },
            },
        },
    },
}


# --- The form, as the model sees it --------------------------------------------------------------------

# Only content fields, each capped. Anything else the browser sends (payment details, images, ids,
# coordinates, sales figures) is dropped before the model sees it.
TEXT_LIMITS = {
    'title': 255, 'tagline': 300, 'category': 60, 'date': 40, 'startTime': 5, 'endTime': 5,
    'venueName': 200, 'venueAddress': 200, 'venueCity': 120, 'description': 3000, 'hostName': 120,
    'hostRole': 120, 'attendeeNote': 500,
}
MAX_TAGS, MAX_AGENDA, MAX_TIERS = 15, 20, 10


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number == int(number) else number


def sanitize_form_state(raw):
    """The organizer's event form reduced to its content fields, each capped to a sensible length."""
    raw = raw if isinstance(raw, dict) else {}
    form = {key: _text(raw.get(key), limit) for key, limit in TEXT_LIMITS.items()}
    form['isFree'] = bool(raw.get('isFree'))
    tags = raw.get('tags')
    if isinstance(tags, str):
        tags = tags.split(',')
    form['tags'] = [_text(t, 40) for t in (tags if isinstance(tags, list) else []) if _text(t, 40)][:MAX_TAGS]
    form['agenda'] = [
        {'time': _text(item.get('time'), 5), 'title': _text(item.get('title'), 200), 'detail': _text(item.get('detail'), 300)}
        for item in (raw.get('agenda') or []) if isinstance(item, dict)
    ][:MAX_AGENDA]
    form['tiers'] = [
        {
            'name': _text(tier.get('name'), 120),
            'price': _number(tier.get('price')),
            'quantity': _number(tier.get('quantity')),
            'admits': _number(tier.get('admits')) or 1,
            'description': _text(tier.get('description'), 300),
            'perks': [_text(p, 80) for p in (tier.get('perks') or []) if isinstance(p, str)][:6]
            if isinstance(tier.get('perks'), list) else _text(tier.get('perks'), 300),
        }
        for tier in (raw.get('tiers') or []) if isinstance(tier, dict)
    ][:MAX_TIERS]
    # Empty values carry no information for the model
    return {key: value for key, value in form.items() if value not in ('', [], None)}


# --- Validating what comes back ---------------------------------------------------------------------------

TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def _whole(value, minimum, maximum):
    """An integer in range, accepting whole floats (3000.0); None otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    return value if minimum <= value <= maximum else None


def _agenda(items, form):
    start, end = form.get('startTime'), form.get('endTime')
    window = start and end and TIME_RE.match(start) and TIME_RE.match(end) and start < end
    clean = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        time, title = str(item.get('time', '')).strip(), str(item.get('title', '')).strip()
        if len(time) == 4 and TIME_RE.match('0' + time):
            time = '0' + time
        if not TIME_RE.match(time) or not title:
            continue
        if window and not (start <= time <= end):
            continue  # Outside the event's start and end times
        clean.append({'time': time, 'title': title[:200], 'detail': str(item.get('detail', '')).strip()[:500]})
    clean.sort(key=lambda i: i['time'])
    return clean or None


def _tiers(items, form):
    clean = []
    for tier in items if isinstance(items, list) else []:
        if not isinstance(tier, dict):
            continue
        name = str(tier.get('name', '')).strip()[:120]
        price = _whole(tier.get('price'), 0, 10_000_000)
        quantity = _whole(tier.get('quantity'), 1, 1_000_000)
        admits = _whole(tier.get('admits', 1) or 1, 1, TicketTier.MAX_ADMITS)
        if not name or price is None or quantity is None or admits is None:
            continue  # Missing name, negative or fractional price, bad quantity
        if form.get('isFree') and price != 0:
            continue
        if not form.get('isFree') and price == 0:
            continue  # Paid events need every ticket type priced above 0 (as the event form does)
        perks = [str(p).strip()[:80] for p in tier.get('perks') or [] if str(p).strip()][:6]
        clean.append({'name': name, 'price': price, 'quantity': quantity, 'admits': admits,
                      'description': str(tier.get('description', '')).strip()[:500], 'perks': perks})
    return clean[:MAX_TIERS] or None


def _tags(items):
    clean, seen = [], set()
    for tag in items if isinstance(items, list) else []:
        tag = str(tag).strip().lstrip('#').strip()
        if not tag or len(tag) > 30 or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        clean.append(tag)
    return clean[:10] or None


TEXT_FIELDS = {'title': 120, 'tagline': 300, 'description': 5000, 'attendeeNote': 500}


def validate_suggestions(raw_suggestions, form):
    """Keeps only suggestions that the event form would accept, in the public {field, label, value} shape."""
    valid = []
    for raw in raw_suggestions if isinstance(raw_suggestions, list) else []:
        if not isinstance(raw, dict) or raw.get('field') not in FIELDS:
            continue
        field = raw['field']
        label = str(raw.get('label') or '').strip()[:60]
        value = None
        if field in TEXT_FIELDS:
            text = str(raw.get('text') or '').strip()
            if text and len(text) <= TEXT_FIELDS[field]:
                value = text
        elif field == 'category':
            text = str(raw.get('text') or '').strip()
            value = text if text in Gathering.CATEGORIES else None
        elif field == 'tags':
            value = _tags(raw.get('items'))
        elif field == 'agenda':
            value = _agenda(raw.get('agenda'), form)
        elif field == 'tiers':
            value = _tiers(raw.get('tiers'), form)
        if value is None:
            continue
        suggestion = {'field': field, 'value': value}
        if label:
            suggestion['label'] = label
        valid.append(suggestion)
    # At most 5 options per text field, one each for the others
    counts, kept = {}, []
    for suggestion in valid:
        field = suggestion['field']
        counts[field] = counts.get(field, 0) + 1
        if counts[field] <= (5 if field in ('title', 'tagline', 'description', 'attendeeNote') else 3):
            kept.append(suggestion)
    return kept
