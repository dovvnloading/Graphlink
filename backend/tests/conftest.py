import pytest

import backend  # noqa: F401 - side effect: backend/__init__.py's own import
# puts graphlink_app/ on sys.path - this must come before the bare
# `import api_provider` below, matching the same convention every other
# file in this test package already follows (see test_canvas.py/
# test_agents.py/test_app_ws.py's own identical comment).
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
