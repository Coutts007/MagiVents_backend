"""Provider registry. AI_PROVIDER=anthropic (default) uses Claude when AI_API_KEY is set; with no key, or with
AI_PROVIDER=mock, the mock provider answers with labelled sample suggestions."""
from django.conf import settings

from .base import AssistantError, AssistantProvider, ProviderRequest, Usage
from .mock import MockProvider


def get_provider():
    if settings.AI_PROVIDER == 'mock' or not settings.AI_API_KEY:
        return MockProvider()
    if settings.AI_PROVIDER == 'anthropic':
        from .claude import ClaudeProvider
        return ClaudeProvider(settings.AI_API_KEY, settings.AI_MODEL, settings.AI_TIMEOUT_SECONDS,
                              settings.AI_WORKSPACE_ID)
    raise AssistantError('The assistant is not set up correctly on the server.', 'provider_unknown', 503, retryable=False)


__all__ = ['AssistantError', 'AssistantProvider', 'ProviderRequest', 'Usage', 'MockProvider', 'get_provider']
