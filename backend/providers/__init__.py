"""ADR-006: the provider abstraction package.

Stage 6.1 introduces the seam: a `Provider` protocol (base.py), a
`FakeProvider` that makes the streaming path testable without a network
(fake.py), and `OllamaProvider` as the first real port (ollama_provider.py).
api_provider.py's chat()/chat_stream() Ollama branches route through it;
the other four providers still live in api_provider's if/elif surface until
stages 6.3+ port them one by one.
"""

from backend.providers.anthropic_provider import AnthropicProvider
from backend.providers.base import (
    CancelToken,
    ChatRequest,
    Provider,
    ProviderCapabilities,
    ProviderEvent,
)
from backend.providers.fake import FakeProvider
from backend.providers.gemini_provider import GeminiProvider
from backend.providers.llama_cpp_provider import LlamaCppProvider
from backend.providers.ollama_provider import OllamaProvider
from backend.providers.openai_provider import OpenAIProvider

__all__ = [
    "AnthropicProvider",
    "CancelToken",
    "ChatRequest",
    "FakeProvider",
    "GeminiProvider",
    "LlamaCppProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderCapabilities",
    "ProviderEvent",
]
