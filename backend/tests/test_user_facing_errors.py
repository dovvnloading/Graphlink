"""Real WebSocket checks for deliberate versus unexpected intent failures."""

import pytest

from backend.domain.model import SceneError, UserFacingError
from backend.tests.test_app_ws import make_client

_GENERIC_ERROR = "Something went wrong while handling this request."
_INTENT = "ui13aFail"
_REQUEST_ID = "ui13a-request"


def _expected_reply(message: str) -> dict:
    return {"kind": "error", "id": _REQUEST_ID, "error": message}


def _error_reply(error: Exception) -> dict:
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        async def fail() -> None:
            raise error

        # The socket uses this real, configured session and dispatches through
        # app.py's intent handler, rather than invoking _handle_message directly.
        client.app.state.bus.session().register_intent("system", _INTENT, fail)
        ws.send_json({
            "kind": "intent",
            "topic": "system",
            "intent": _INTENT,
            "args": [],
            "id": _REQUEST_ID,
        })
        return ws.receive_json()


def test_scene_error_reaches_caller_and_remains_a_value_error():
    assert issubclass(SceneError, ValueError)
    assert issubclass(SceneError, UserFacingError)
    message = "Export requires a saved result."
    assert _error_reply(SceneError(message)) == _expected_reply(message)


def test_runtime_error_is_generic_and_does_not_expose_sensitive_text():
    reply = _error_reply(RuntimeError("secret-token-123 /home/user/x"))
    assert reply == _expected_reply(_GENERIC_ERROR)
    assert "secret-token-123" not in str(reply)
    assert "/home/user/x" not in str(reply)


def test_user_facing_error_whitespace_is_collapsed_to_one_line():
    reply = _error_reply(UserFacingError("  First line\n\tSecond\r\n  third   line  "))
    assert reply == _expected_reply("First line Second third line")


def test_user_facing_error_is_capped_at_300_characters():
    reply = _error_reply(UserFacingError("x" * 1000))
    assert reply == _expected_reply("x" * 299 + "…")
    assert len(reply["error"]) == 300
    assert reply["error"].endswith("…")


@pytest.mark.parametrize("message", ["", " \n\t "])
def test_empty_user_facing_error_falls_back_to_generic_text(message: str):
    assert _error_reply(UserFacingError(message)) == _expected_reply(_GENERIC_ERROR)
