import json
import logging

from django.http import StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .prompts import ACTION_INSTRUCTIONS
from .providers import AssistantError
from .services import run_turn
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
            events = run_turn(request.user, values['messages'], values['formState'], values['action'])
        except AssistantError as exc:
            return Response(exc.payload(), status=exc.status)

        def stream():
            try:
                for event, payload in events:
                    yield sse(event, payload)
            except AssistantError as exc:
                yield sse('error', exc.payload())
            except Exception:  # noqa: BLE001 - the form must never break because the assistant failed
                logger.exception('assistant turn failed for user=%s', request.user.id)
                yield sse('error', {'error': 'Something went wrong with the assistant. Please try again.',
                                    'code': 'internal', 'retryable': True})

        response = StreamingHttpResponse(stream(), content_type='text/event-stream; charset=utf-8')
        response['Cache-Control'] = 'no-cache'
        # nginx: pass events through as they are written instead of buffering the response
        response['X-Accel-Buffering'] = 'no'
        return response
