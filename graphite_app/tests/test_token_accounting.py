"""Tests for handle_response()'s input-token accounting.

Regression coverage for doc/ARCHITECTURE_REVIEW_FINDINGS.md #41: handle_response() used
to recompute input_tokens by parsing self.token_counter_widget.input_label.text() (a
QLabel's *displayed* string, with formatting like "1,234" stripped by hand) instead of
using the actual integer value already computed in send_message(). A display widget was
the source of truth for a running token-total calculation. send_message() now passes
that value straight through the finished-signal connection instead.

Uses a MagicMock as `self` for this unbound-method call (matching the pattern in
test_shutdown_thread_enumeration.py) with token_counter_widget.input_label deliberately
"poisoned" with a wrong value - if the fix regresses back to parsing the label, this
poisoned value would show up in total_session_tokens instead of the real one passed in.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import graphite_window_actions


def _make_window_and_user_node():
    window = MagicMock()
    shared_scene = MagicMock()
    window.chat_view.scene.return_value = shared_scene
    window.chat_thread = None
    window.total_session_tokens = 100
    window.token_estimator.count_tokens.return_value = 50  # output_tokens
    # Deliberately wrong - if handle_response ever reads this again, the assertion
    # on total_session_tokens below will catch it.
    window.token_counter_widget.input_label.text.return_value = "99,999"
    window._parse_response.return_value = []

    user_node = MagicMock()
    user_node.scene.return_value = shared_scene

    return window, user_node


class TestHandleResponseUsesThePassedInTokenCount:
    def test_total_session_tokens_uses_the_explicit_input_tokens_argument(self):
        window, user_node = _make_window_and_user_node()

        graphite_window_actions.WindowActionsMixin.handle_response(
            window,
            {"role": "assistant", "content": "hi there"},
            user_node,
            [],
            25,  # input_tokens - the real value, distinct from the poisoned widget text
            worker_thread=None,
        )

        # 100 (starting total) + 25 (real input_tokens) + 50 (output_tokens) = 175.
        # If the old widget-parsing bug were still present, this would be
        # 100 + 99999 + 50 instead.
        assert window.total_session_tokens == 175

    def test_the_token_counter_widgets_displayed_text_is_never_read(self):
        window, user_node = _make_window_and_user_node()

        graphite_window_actions.WindowActionsMixin.handle_response(
            window,
            {"role": "assistant", "content": "hi there"},
            user_node,
            [],
            25,
            worker_thread=None,
        )

        window.token_counter_widget.input_label.text.assert_not_called()

    def test_zero_input_tokens_is_handled_correctly(self):
        window, user_node = _make_window_and_user_node()

        graphite_window_actions.WindowActionsMixin.handle_response(
            window,
            {"role": "assistant", "content": "hi there"},
            user_node,
            [],
            0,
            worker_thread=None,
        )

        assert window.total_session_tokens == 150  # 100 + 0 + 50
