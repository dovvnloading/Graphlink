"""Qt-free port of graphlink_app/graphlink_window_actions.py's _parse_response.

This is the shared parsing routine legacy uses for every REAL chat reply -
both the ordinary send path (WindowActionsMixin.handle_response) and the
regenerate path (WindowActionsMixin.handle_regenerated_response) both call
`self._parse_response(response_text)` before creating any node. It splits a
flat LLM response string into an ordered list of thinking/text/code parts, so
the caller can create a real chat node for the text plus separate real
thinking-kind and code-kind CHILD nodes for the rest - instead of dumping the
model's raw, unprocessed reply (complete with <think> tags, <code_block>
wrappers, and/or ```fenced``` code) into a single flat node.

Both real paths now depend on this module: the ordinary send path
(backend/canvas.py's send_message, R4.3b) and the regenerate path
(backend/canvas.py's regenerate_response, R4.3c) each call parse_response
directly. ConversationNode remains the one confirmed exemption -
ConversationNode never called _parse_response in legacy either, see
backend/canvas.py's send_conversation_message for that.
"""

from __future__ import annotations

import re

import api_provider

# Compile once at module import. A fence's entire info line is separate from
# its code body; the first whitespace-delimited info token is the language.
# Match complete fence lines and require a closing fence at least as long as
# the opener, so literal shorter fences inside code are not delimiters.
CODE_BLOCK_TAG_PATTERN = re.compile(r"<code_block>([\s\S]*?)</code_block>", re.IGNORECASE)
CODE_FENCE_PATTERN = re.compile(
    r"^[ \t]*(?P<fence>`{3,})(?P<info>[^`\r\n]*)\r?\n"
    r"(?P<code>[\s\S]*?)^[ \t]*(?P=fence)`*[ \t]*(?=\r?$)",
    re.MULTILINE,
)

PLACEHOLDER_GENERATED_CONTENT = "[Generated Content]"
PLACEHOLDER_ASSISTANT_REASONING = "[Assistant Reasoning]"
PLACEHOLDER_EMPTY_RESPONSE = "[Empty Response]"


def _fence_language(match: re.Match[str]) -> str:
    tokens = match.group("info").split(maxsplit=1)
    return tokens[0] if tokens else ""


def model_visible_reply(response_text: str, displayed: str) -> str | None:
    """The reply as the model wrote it, minus private reasoning.

    Returned only when it differs from what the chat bubble displays (code
    moved to cards, or a placeholder), so ordinary replies store nothing
    extra. Fences keep their original order and language (#1099).
    """
    _reasoning, visible = api_provider.split_reasoning_and_content(response_text)
    visible = visible.strip()
    return visible if visible and visible != displayed else None


def parse_response(response_text: str) -> list[dict]:
    """Split a flat LLM reply into ordered thinking/text/code parts.

    Based on graphlink_window_actions.py's WindowActionsMixin._parse_response.
    The returned list, when multiple types are present, is always ordered
    thinking (if any), then text (if any), then code (if any) - never
    interleaved, never more than one of each type. The "language" key exists
    ONLY on code-type parts; thinking/text parts have exactly the two keys
    {"type", "content"}.
    """
    parts: list[dict] = []

    thinking_content, remaining_text = api_provider.split_reasoning_and_content(response_text)
    if thinking_content:
        parts.append({"type": "thinking", "content": thinking_content})

    text_content = ""
    code_snippets: list[str] = []
    language = ""

    code_block_match = CODE_BLOCK_TAG_PATTERN.search(remaining_text)
    if code_block_match:
        code_content_raw = code_block_match.group(1).strip()
        text_content = (
            remaining_text[: code_block_match.start()] + remaining_text[code_block_match.end() :]
        ).strip()
        inner_matches = list(CODE_FENCE_PATTERN.finditer(code_content_raw))
        if inner_matches:
            language = _fence_language(inner_matches[0])
            code_snippets = [m.group("code").strip() for m in inner_matches]
        else:
            code_snippets = [code_content_raw]
    else:
        matches = list(CODE_FENCE_PATTERN.finditer(remaining_text))
        if matches:
            language = _fence_language(matches[0])
            code_snippets = [m.group("code").strip() for m in matches]
            text_content = CODE_FENCE_PATTERN.sub("", remaining_text).strip()
        else:
            text_content = remaining_text.strip()

    if text_content:
        parts.append({"type": "text", "content": text_content})

    if code_snippets:
        combined_code = "\n\n# --- Next Code Block ---\n\n".join(code_snippets).strip()
        if combined_code:
            parts.append({"type": "code", "language": language, "content": combined_code})

    if not parts and response_text.strip():
        return [{"type": "text", "content": response_text.strip()}]

    return parts
