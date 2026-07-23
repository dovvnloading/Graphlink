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

# Compiled once at module import instead of once per call (legacy's own
# _parse_response re-compiles both patterns on every invocation) - a harmless
# efficiency improvement over legacy, not a behavior change.
CODE_BLOCK_TAG_PATTERN = re.compile(r"<code_block>([\s\S]*?)</code_block>", re.IGNORECASE)
CODE_FENCE_PATTERN = re.compile(r"```(\w*)\s*\n?([\s\S]*?)\s*```")

PLACEHOLDER_GENERATED_CONTENT = "[Generated Content]"
PLACEHOLDER_ASSISTANT_REASONING = "[Assistant Reasoning]"
PLACEHOLDER_EMPTY_RESPONSE = "[Empty Response]"


def parse_response(response_text: str) -> list[dict]:
    """Split a flat LLM reply into ordered thinking/text/code parts.

    Exact port of graphlink_window_actions.py's WindowActionsMixin._parse_response.
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
            language = inner_matches[0].group(1).strip()
            code_snippets = [m.group(2).strip() for m in inner_matches]
        else:
            code_snippets = [code_content_raw]
    else:
        matches = list(CODE_FENCE_PATTERN.finditer(remaining_text))
        if matches:
            language = matches[0].group(1).strip()
            code_snippets = [m.group(2).strip() for m in matches]
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
