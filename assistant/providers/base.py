"""
The small interface every AI provider implements. A provider streams JSON text that follows
assistant.schema.OUTPUT_SCHEMA; everything else (the reply streaming, validation, limits) is shared.
To add a provider (OpenAI, a local model, ...), subclass AssistantProvider and register it in
assistant/providers/__init__.py.
"""
from dataclasses import dataclass, field


# What organizers see for any failure on the server's side; details go to the server log only
SERVER_ERROR = 'Something went wrong on our side. Please try again in a moment.'


class AssistantError(Exception):
    """A failure the organizer can be told about. `code` is stable for the frontend."""

    def __init__(self, message, code, status=503, retryable=True):
        super().__init__(message)
        self.message, self.code, self.status, self.retryable = message, code, status, retryable

    def payload(self):
        return {'error': self.message, 'code': self.code, 'retryable': self.retryable}


@dataclass
class ProviderRequest:
    system: str
    #: Anthropic-style messages: [{"role": "user"|"assistant", "content": str | [text blocks]}]
    messages: list
    schema: dict
    #: Also passed so a provider without a model (the mock) can answer sensibly
    action: str = 'chat'
    form: dict = field(default_factory=dict)
    idea: str = ''


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ''


class AssistantProvider:
    name = ''
    #: True for providers whose suggestions are canned samples, not written by a model
    is_sample = False

    async def stream(self, request):
        """An async generator: yields ("text", chunk) as the JSON answer arrives, ("reset", None) when the answer
        restarts and what came before must be discarded, and finally ("done", Usage). Raises AssistantError on
        failure. It must not block: thousands of these share one event loop per server process."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator
