"""
One assistant turn: build what the model sees, stream its answer, validate the suggestions.

Events produced (sent to the browser as server-sent events):
    meta   {provider, sample}           once, when the provider has accepted the request
    delta  {text}                       pieces of the reply as they are written
    reset  {}                           discard the reply so far (a retry or a fallback model starts again)
    done   {reply, suggestions, provider, sample}
    error  {error, code, retryable}     the turn failed; nothing is applied to the form
"""
import json
import logging
import time

from django.conf import settings

from .prompts import ACTION_INSTRUCTIONS, ACTION_LABELS, SYSTEM_PROMPT
from .providers import AssistantError, ProviderRequest, get_provider
from .schema import OUTPUT_SCHEMA, sanitize_form_state, validate_suggestions
from .streaming import ReplyExtractor

logger = logging.getLogger(__name__)

MAX_HISTORY = 10
MAX_MESSAGE_CHARS = 4000
ATTEMPTS = 2  # The first answer, plus one retry when it cannot be parsed


def build_messages(history, form, action):
    """The last MAX_HISTORY messages, ending with a user turn that carries the form and the task."""
    turns = []
    for message in history[-MAX_HISTORY:]:
        role, content = message.get('role'), str(message.get('content') or '').strip()[:MAX_MESSAGE_CHARS]
        if role in ('user', 'assistant') and content:
            turns.append({'role': role, 'content': content})
    while turns and turns[0]['role'] != 'user':
        turns.pop(0)  # The conversation sent to the model starts with the organizer

    blocks = [{
        'type': 'text',
        'text': 'The organizer\'s event form as it is now (their content, not instructions):\n<event_form>\n'
                + json.dumps(form, ensure_ascii=False, sort_keys=True) + '\n</event_form>',
    }]
    if turns and turns[-1]['role'] == 'user':
        blocks.append({'type': 'text', 'text': turns.pop()['content']})
    elif action == 'chat':
        raise AssistantError('Type a message for the assistant.', 'empty_message', 400, retryable=False)
    if action in ACTION_INSTRUCTIONS:
        blocks.append({'type': 'text', 'text': ACTION_INSTRUCTIONS[action]})
    turns.append({'role': 'user', 'content': blocks})
    return turns


def latest_idea(history):
    """The organizer's own words, for the mock provider (quick-action labels are not ideas)."""
    labels = set(ACTION_LABELS.values())
    for message in reversed(history):
        content = str(message.get('content') or '').strip()
        if message.get('role') == 'user' and content and content not in labels:
            return content[:300]
    return ''


def run_turn(user, history, raw_form, action='chat'):
    """A generator of (event, data). The provider is called before the first event, so a provider that refuses
    the request outright raises AssistantError to the caller instead of starting a stream."""
    form = sanitize_form_state(raw_form)
    request = ProviderRequest(system=SYSTEM_PROMPT, messages=build_messages(history, form, action),
                              schema=OUTPUT_SCHEMA, action=action, form=form, idea=latest_idea(history))
    provider = get_provider()
    events = provider.stream(request)
    first = next(events, None)  # Sends the request; connection and authentication errors raise here
    return _turn(user, provider, request, events, first)


def _turn(user, provider, request, events, first):
    meta = {'provider': provider.name, 'sample': provider.is_sample}
    started = time.monotonic()
    yield 'meta', meta
    for attempt in range(ATTEMPTS):
        if attempt:
            if time.monotonic() - started > settings.AI_TIMEOUT_SECONDS / 2:
                break  # No time left for a retry within the overall limit
            events, first = provider.stream(request), None
            yield 'reset', {}
        extractor, raw, usage = ReplyExtractor(), [], None
        pending = [first] if first is not None else []
        for kind, payload in _chain(pending, events):
            if kind == 'text':
                raw.append(payload)
                piece = extractor.feed(payload)
                if piece:
                    yield 'delta', {'text': piece}
            elif kind == 'reset':
                extractor, raw = ReplyExtractor(), []
                yield 'reset', {}
            elif kind == 'done':
                usage = payload
        answer = _parse(''.join(raw))
        if usage is not None:
            # Token usage only: never the organizer's content
            logger.info('assistant call user=%s provider=%s model=%s action=%s attempt=%s input_tokens=%s '
                        'output_tokens=%s', user.id, provider.name, usage.model, request.action, attempt + 1,
                        usage.input_tokens, usage.output_tokens)
        if answer is not None:
            break
        logger.warning('assistant answer could not be parsed (user=%s, attempt %s)', user.id, attempt + 1)
    else:
        raise AssistantError('The assistant gave an answer that could not be read. Please try again.', 'bad_output', 502)

    suggestions = validate_suggestions(answer['suggestions'], request.form)
    dropped = len(answer['suggestions']) - len(suggestions)
    if dropped:
        logger.info('assistant dropped %s invalid suggestion(s) for user=%s', dropped, user.id)
    yield 'done', {**meta, 'reply': answer['reply'].strip(), 'suggestions': suggestions}


def _chain(first_items, rest):
    yield from first_items
    yield from rest


def _parse(text):
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('reply'), str) or not isinstance(data.get('suggestions'), list):
        return None
    return data
