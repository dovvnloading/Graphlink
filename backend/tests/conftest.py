import pytest

import backend  # noqa: F401 - exercises the package import
# R7.2: api_provider.py sits at the repo root, a sibling of backend/ - the
# same directory pytest already put on sys.path to make `backend` itself
# importable, so this needs no setup and no particular ordering relative to
# the import above.
import api_provider


@pytest.fixture(autouse=True)
def _chat_stream_delegates_to_patched_chat(monkeypatch):
    """R4.4: send_message's reply path now always calls api_provider.chat_stream
    (AgentDispatcher.start_chat_reply passes stream=True unconditionally), not
    api_provider.chat. Every existing test in this suite fakes only chat() via
    patch.object(api_provider, "chat", fake_chat) - without this fixture,
    chat_stream's real Ollama branch runs instead (these tests configure Ollama
    mode to match production), attempting a genuine network call.

    This generic chat_stream fake looks up api_provider.chat AT CALL TIME (a
    fresh module-attribute read, not a captured reference), so it transparently
    picks up whatever fake_chat a given test has patched into api_provider.chat
    for the duration of its own `with patch.object(...)` block, and forwards it
    through on_chunk as a single synthetic chunk - the exact shape
    chat_stream's own documented non-Ollama fallback already uses. This tests
    send_message's downstream logic (node creation, parsing, cancellation),
    which is unaffected by whether the reply arrived in one chunk or many -
    real incremental chunking is covered separately by
    graphlink_app/tests/test_api_provider_chat_stream.py and this suite's own
    dedicated streaming tests in test_agents.py."""

    def _generic_chat_stream(task, messages, on_chunk, **kwargs):
        response = api_provider.chat(task, messages, **kwargs)
        on_chunk(response["message"].get("content", ""), False)
        return response

    monkeypatch.setattr(api_provider, "chat_stream", _generic_chat_stream)
    yield
