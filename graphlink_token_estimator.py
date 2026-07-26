"""Token counting for chat/context payloads.

Domain logic, not UI - moved out of graphlink_widgets/tokens.py (Phase 2,
TokenCounterWidget island migration), which is where it lived only because
it was originally introduced alongside the widget that displays its output.
Nothing here has ever imported Qt.
"""

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


class TokenEstimator:
    _instance = None
    _encoding = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenEstimator, cls).__new__(cls)
            if TIKTOKEN_AVAILABLE:
                try:
                    cls._encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    cls._encoding = None
                    print("Warning: tiktoken installed, but failed to get encoding. Falling back to character count.")
        return cls._instance

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encoding:
            return len(self._encoding.encode(text))
        else:
            return len(text) // 4
