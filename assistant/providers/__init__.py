"""Provider registry. AI_PROVIDER=anthropic (default) uses Claude when AI_API_KEY is set; with no key, or with
AI_PROVIDER=mock, the mock provider answers with labelled sample suggestions."""
import sys

from django.conf import settings

from .base import SERVER_ERROR, AssistantError, AssistantProvider, ProviderRequest, Usage
from .mock import MockProvider


async def close_clients():
    """Closes the provider connections of the running event loop."""
    if 'assistant.providers.claude' in sys.modules:
        await sys.modules['assistant.providers.claude'].close_clients()


def get_provider():
    if settings.AI_PROVIDER == 'mock' or not settings.AI_API_KEY:
        return MockProvider()
    if settings.AI_PROVIDER == 'anthropic':
        from .claude import ClaudeProvider
        return ClaudeProvider(settings.AI_API_KEY, settings.AI_MODEL, settings.AI_TIMEOUT_SECONDS,
                              settings.AI_WORKSPACE_ID, settings.AI_MAX_CONNECTIONS)
    raise AssistantError(SERVER_ERROR, 'provider_unknown', 503, retryable=False)


__all__ = ['AssistantError', 'AssistantProvider', 'ProviderRequest', 'Usage', 'MockProvider', 'get_provider']
