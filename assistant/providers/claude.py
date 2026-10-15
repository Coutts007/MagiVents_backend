"""Anthropic Claude through the official Python SDK (async client), with structured output and streaming."""
import asyncio
import importlib.util
import logging
import time
import weakref

import anthropic
import httpx2

from .base import SERVER_ERROR, AssistantError, AssistantProvider, Usage

logger = logging.getLogger(__name__)

# Models that accept the server-side refusal fallback (`fallbacks: "default"`); for others it is left off
FALLBACK_MODELS = {'claude-opus-5-5', 'claude-opus-5', 'claude-fable-5-1', 'claude-sonnet-5-5'}
MAX_TOKENS = 8000
HAS_AIOHTTP = importlib.util.find_spec('aiohttp') is not None

# One client per event loop, shared by every request that loop serves, so calls reuse open HTTPS connections
# instead of paying a new TLS handshake each time. Under the ASGI server there is one loop per worker process.
_clients = weakref.WeakKeyDictionary()


async def close_clients():
    """Closes the clients of the running loop (for a loop that is about to be closed)."""
    for client in _clients.pop(asyncio.get_running_loop(), {}).values():
        await client.close()


class ClaudeProvider(AssistantProvider):
    name = 'anthropic'

    def __init__(self, api_key, model, timeout, workspace_id='', max_connections=1000, client=None):
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self.workspace_id, self.max_connections = workspace_id, max_connections
        self._client = client

    @property
    def client(self):
        if self._client is not None:
            return self._client
        key = (self.api_key, self.workspace_id, self.timeout, self.max_connections)
        clients = _clients.setdefault(asyncio.get_running_loop(), {})
        if key not in clients:
            clients[key] = self.make_client()
        return clients[key]

    def make_client(self):
        # A key that is not scoped to one workspace must name the workspace on every request
        headers = {'anthropic-workspace-id': self.workspace_id} if self.workspace_id else None
        # Every streaming answer holds one connection; requests beyond the limit wait for a free one
        limits = httpx2.Limits(max_connections=self.max_connections,
                               max_keepalive_connections=self.max_connections)
        # The SDK's aiohttp transport uses about a third less CPU per streamed answer than its default httpx one;
        # an image built before aiohttp was added keeps working on httpx
        transport = anthropic.DefaultAioHttpClient if HAS_AIOHTTP else anthropic.DefaultAsyncHttpxClient
        http_client = transport(limits=limits)
        # One retry at most, so a failing call is reported well within the organizer's patience
        return anthropic.AsyncAnthropic(api_key=self.api_key, timeout=self.timeout, max_retries=1,
                                        default_headers=headers, http_client=http_client)

    async def stream(self, request):
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
            async with self.client.beta.messages.stream(**params) as stream:
                async for event in stream:
                    if time.monotonic() > deadline:
                        raise AssistantError(SERVER_ERROR, 'timeout', 504)
                    if event.type == 'content_block_start' and getattr(event.content_block, 'type', '') == 'fallback':
                        # Another model takes over from the start: whatever streamed so far is discarded
                        yield 'reset', None
                    elif event.type == 'content_block_delta' and getattr(event.delta, 'type', '') == 'text_delta':
                        yield 'text', event.delta.text
                message = await stream.get_final_message()
        except AssistantError:
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            logger.error('The AI provider rejected the API key (AI_API_KEY)')
            raise AssistantError(SERVER_ERROR,
                                 'provider_auth', 503, retryable=False)
        except anthropic.RateLimitError:
            raise AssistantError(SERVER_ERROR, 'provider_rate_limited', 503)
        except anthropic.APITimeoutError:
            raise AssistantError(SERVER_ERROR, 'timeout', 504)
        except anthropic.BadRequestError as exc:
            logger.error('The AI provider refused the request: %s', exc.message)
            if 'workspace' in exc.message.lower() or 'api key' in exc.message.lower():
                # A setup problem (e.g. AI_WORKSPACE_ID missing), not something the organizer can fix
                raise AssistantError(SERVER_ERROR,
                                     'provider_auth', 503, retryable=False)
            raise AssistantError(SERVER_ERROR, 'provider_rejected', 502)
        except anthropic.APIStatusError as exc:
            logger.warning('AI provider error %s', exc.status_code)
            raise AssistantError(SERVER_ERROR, 'provider_unavailable', 503)
        except anthropic.APIConnectionError:
            raise AssistantError(SERVER_ERROR, 'provider_unavailable', 503)
        # The connection failing after the answer has started (the SDK raises these as they are)
        except httpx2.TimeoutException:
            raise AssistantError(SERVER_ERROR, 'timeout', 504)
        except httpx2.TransportError:
            raise AssistantError(SERVER_ERROR, 'provider_unavailable', 503)

        if message.stop_reason == 'refusal':
            raise AssistantError('The assistant cannot help with that request.', 'refused', 422, retryable=False)
        usage = message.usage
        yield 'done', Usage(input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0,
                            model=getattr(message, 'model', self.model) or self.model)
