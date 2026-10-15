import asyncio
import json
import logging

from django.core.handlers.asgi import ASGIRequest
from django.db import connections
from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .prompts import ACTION_INSTRUCTIONS
from .providers import SERVER_ERROR, AssistantError, close_clients
from .services import prepare_turn, start_turn
from .streaming import sse

logger = logging.getLogger(__name__)

ACTIONS = ['chat', *ACTION_INSTRUCTIONS]


class CanUseAssistant(permissions.BasePermission):
    """The same people who can create events: organizers and admins, not suspended."""
    message = 'The assistant is for organizers. Become an organizer to create events first.'

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_organizer):
            return False
        if user.is_suspended:
            self.message = 'Your organizer account is suspended, so the assistant is not available.'
            return False
        return True


class EventStreamRenderer(BaseRenderer):
    """Lets the browser ask for text/event-stream. The stream itself is a StreamingHttpResponse; this only
    renders the JSON errors returned before a stream starts."""
    media_type = 'text/event-stream'
    format = 'sse'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return json.dumps(data).encode()


class MessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=['user', 'assistant'])
    content = serializers.CharField(max_length=8000, allow_blank=True, trim_whitespace=False)


class AssistantRequestSerializer(serializers.Serializer):
    messages = MessageSerializer(many=True, required=False, default=list, max_length=100)
    formState = serializers.DictField(required=False, default=dict)
    action = serializers.ChoiceField(choices=ACTIONS, required=False, default='chat')


@extend_schema(request=AssistantRequestSerializer, responses={(200, 'text/event-stream'): OpenApiTypes.STR})
class EventAssistantView(APIView):
    """Suggestions for the event form, streamed as server-sent events (see assistant/services.py).
    Errors before the stream starts are plain JSON: {error, code, retryable}."""
    permission_classes = [CanUseAssistant]
    renderer_classes = [JSONRenderer, EventStreamRenderer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'assistant'

    def post(self, request):
        data = AssistantRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            turn = prepare_turn(values['messages'], values['formState'], values['action'])
        except AssistantError as exc:
            return Response(exc.payload(), status=exc.status)
        user_id = request.user.id
        # An answer takes seconds to write and needs no database, so the connection goes back now instead of
        # being held by every open stream
        release_database_connections()

        if isinstance(request._request, ASGIRequest):
            # Under the ASGI server the answer streams on the event loop: an open stream holds no thread or
            # worker, so one process serves thousands at once. Provider errors arrive as an `error` event.
            body = async_events(user_id, turn)
        else:
            # Under a WSGI server (runserver, tests) the same async code runs on a private loop in this thread
            try:
                body = sync_events(user_id, turn)
            except AssistantError as exc:
                return Response(exc.payload(), status=exc.status)

        response = StreamingHttpResponse(body, content_type='text/event-stream; charset=utf-8')
        response['Cache-Control'] = 'no-cache'
        # nginx: pass events through as they are written instead of buffering the response
        response['X-Accel-Buffering'] = 'no'
        return response


def release_database_connections():
    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close()


def error_event(exc, user_id):
    if isinstance(exc, AssistantError):
        return sse('error', exc.payload())
    # The form must never break because the assistant failed
    logger.error('assistant turn failed for user=%s', user_id, exc_info=exc)
    return sse('error', {'error': SERVER_ERROR, 'code': 'internal', 'retryable': True})


async def async_events(user_id, turn):
    try:
        async for event, payload in await start_turn(user_id, turn):
            yield sse(event, payload)
    except Exception as exc:  # noqa: BLE001
        yield error_event(exc, user_id)


def sync_events(user_id, turn):
    """Starts the turn now (so a provider that refuses it raises AssistantError here), then streams it."""
    loop = asyncio.new_event_loop()
    try:
        events = loop.run_until_complete(start_turn(user_id, turn))
    except BaseException:
        close_loop(loop)
        raise

    def stream():
        try:
            while True:
                try:
                    event, payload = loop.run_until_complete(anext(events))
                except StopAsyncIteration:
                    return
                yield sse(event, payload)
        except Exception as exc:  # noqa: BLE001
            yield error_event(exc, user_id)
        finally:
            loop.run_until_complete(events.aclose())
            close_loop(loop)
    return stream()


def close_loop(loop):
    loop.run_until_complete(close_clients())
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
