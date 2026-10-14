"""Anthropic Claude through the official Python SDK, with structured output and streaming."""
import logging
import time

import anthropic

from .base import AssistantError, AssistantProvider, Usage

logger = logging.getLogger(__name__)

# Models that accept the server-side refusal fallback (`fallbacks: "default"`); for others it is left off
FALLBACK_MODELS = {'claude-opus-5-5', 'claude-opus-5', 'claude-fable-5-1', 'claude-sonnet-5-5'}
MAX_TOKENS = 8000


class ClaudeProvider(AssistantProvider):
    name = 'anthropic'

    def __init__(self, api_key, model, timeout, workspace_id=''):
        self.model = model
        self.timeout = timeout
        # A key that is not scoped to one workspace must name the workspace on every request
        headers = {'anthropic-workspace-id': workspace_id} if workspace_id else None
        # One retry at most, so a failing call is reported well within the organizer's patience
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1, default_headers=headers)

    def stream(self, request):
        params = {
            'model': self.model,
            'max_tokens': MAX_TOKENS,
            # The system prompt never changes, so it can be served from the prompt cache
            'system': [{'type': 'text', 'text': request.system, 'cache_control': {'type': 'ephemeral'}}],
            'messages': request.messages,
            'output_config': {
                # Copywriting for a chat panel: quick answers matter more than deep reasoning
                'effort': 'low',
                # The answer must be JSON in exactly this shape
                'format': {'type': 'json_schema', 'schema': request.schema},
            },
        }
        if self.model in FALLBACK_MODELS:
            # A request a safety classifier declines is re-run on Anthropic's recommended fallback model
            params.update(betas=['server-side-fallback-2026-07-01'], fallbacks='default')
        deadline = time.monotonic() + self.timeout
        try:
            with self.client.beta.messages.stream(**params) as stream:
                for event in stream:
                    if time.monotonic() > deadline:
                        raise AssistantError('The assistant took too long to answer. Please try again.', 'timeout', 504)
                    if event.type == 'content_block_start' and getattr(event.content_block, 'type', '') == 'fallback':
                        # Another model takes over from the start: whatever streamed so far is discarded
                        yield 'reset', None
                    elif event.type == 'content_block_delta' and getattr(event.delta, 'type', '') == 'text_delta':
                        yield 'text', event.delta.text
                message = stream.get_final_message()
        except AssistantError:
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            logger.error('The AI provider rejected the API key (AI_API_KEY)')
            raise AssistantError('The assistant is not set up correctly on the server. Please tell MagiVents.',
                                 'provider_auth', 503, retryable=False)
        except anthropic.RateLimitError:
            raise AssistantError('The assistant is busy right now. Please try again in a minute.', 'provider_rate_limited', 503)
        except anthropic.APITimeoutError:
            raise AssistantError('The assistant took too long to answer. Please try again.', 'timeout', 504)
        except anthropic.BadRequestError as exc:
            logger.error('The AI provider refused the request: %s', exc.message)
            if 'workspace' in exc.message.lower() or 'api key' in exc.message.lower():
                # A setup problem (e.g. AI_WORKSPACE_ID missing), not something the organizer can fix
                raise AssistantError('The assistant is not set up correctly on the server. Please tell MagiVents.',
                                     'provider_auth', 503, retryable=False)
            raise AssistantError('The assistant could not handle that request. Please try again, or tell MagiVents '
                                 'if it keeps happening.', 'provider_rejected', 502)
        except anthropic.APIStatusError as exc:
            logger.warning('AI provider error %s', exc.status_code)
            raise AssistantError('The assistant is unavailable right now. Please try again shortly.', 'provider_unavailable', 503)
        except anthropic.APIConnectionError:
            raise AssistantError('The assistant is unavailable right now. Please try again shortly.', 'provider_unavailable', 503)

        if message.stop_reason == 'refusal':
            raise AssistantError('The assistant cannot help with that request.', 'refused', 422, retryable=False)
        usage = message.usage
        yield 'done', Usage(input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0,
                            model=getattr(message, 'model', self.model) or self.model)
