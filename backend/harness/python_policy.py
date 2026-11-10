"""Input and consent policy for the persistent Python tool.

This is an approval boundary, not an isolation mechanism. Python executes
with the local process's privileges, and identical source can have different
effects as interpreter state changes.
"""

from __future__ import annotations

import unicodedata

PYTHON_SOURCE_MAX_CHARS = 20_000


def source_error(arguments: dict) -> str | None:
    """Refuse undisclosable inputs; never coerce, strip or truncate source."""
    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        return "python.exec needs non-empty code as a string."
    if len(code) > PYTHON_SOURCE_MAX_CHARS:
        return f"Python source exceeds {PYTHON_SOURCE_MAX_CHARS} characters; nothing was executed."
    for character in code:
        if character not in "\t\r\n" and unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            return (
                f"Python source contains unsupported invisible character U+{ord(character):04X}. "
                "Use a visible escape sequence in a string literal; nothing was executed."
            )
    return None
