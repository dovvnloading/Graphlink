"""Isolated unit tests for backend/response_parsing.py's parse_response().

No SceneDocument, no dispatcher, no WS bus - just the pure parsing function,
verified against the exact behaviors ported from legacy's
WindowActionsMixin._parse_response (graphlink_app/graphlink_window_actions.py).
"""

from backend.response_parsing import parse_response


def test_empty_input_returns_empty_list():
    assert parse_response("") == []


def test_whitespace_only_input_returns_empty_list():
    assert parse_response("   \n\t  ") == []


def test_plain_text_with_no_code_or_thinking_returns_one_text_part():
    result = parse_response("  Hello, just plain text.  ")
    assert result == [{"type": "text", "content": "Hello, just plain text."}]


def test_single_fenced_code_block_with_language_produces_text_then_code():
    response = "Here is the answer:\n\n```python\nprint('hi')\n```"
    result = parse_response(response)
    assert result == [
        {"type": "text", "content": "Here is the answer:"},
        {"type": "code", "language": "python", "content": "print('hi')"},
    ]


def test_two_fenced_code_blocks_collapse_into_one_combined_code_part():
    response = (
        "Two snippets:\n"
        "```python\nprint('one')\n```\n"
        "and\n"
        "```javascript\nconsole.log('two')\n```"
    )
    result = parse_response(response)
    text_parts = [p for p in result if p["type"] == "text"]
    code_parts = [p for p in result if p["type"] == "code"]
    assert len(code_parts) == 1
    code_part = code_parts[0]
    assert code_part["language"] == "python", "language comes from the FIRST fence only"
    assert code_part["content"] == (
        "print('one')\n\n# --- Next Code Block ---\n\nconsole.log('two')"
    )
    assert len(text_parts) == 1
    assert "```" not in text_parts[0]["content"]


def test_code_block_tag_with_nested_fences_takes_the_inner_fence_branch():
    response = (
        "<code_block>\n"
        "```python\n"
        "print('nested')\n"
        "```\n"
        "</code_block>"
    )
    result = parse_response(response)
    code_parts = [p for p in result if p["type"] == "code"]
    assert len(code_parts) == 1
    assert code_parts[0]["language"] == "python"
    assert code_parts[0]["content"] == "print('nested')"


def test_code_block_tag_with_no_inner_fences_produces_one_raw_blob_with_empty_language():
    response = "<code_block>raw code, no fences here</code_block>"
    result = parse_response(response)
    code_parts = [p for p in result if p["type"] == "code"]
    assert len(code_parts) == 1
    assert code_parts[0]["language"] == ""
    assert code_parts[0]["content"] == "raw code, no fences here"


def test_think_tag_with_trailing_text_and_code_fence_produces_fixed_order():
    response = (
        "<think>reasoning about the problem</think>\n"
        "Here's the answer.\n"
        "```python\nprint('done')\n```"
    )
    result = parse_response(response)
    assert [p["type"] for p in result] == ["thinking", "text", "code"]
    assert result[0]["content"] == "reasoning about the problem"
    assert result[1]["content"] == "Here's the answer."
    assert result[2]["language"] == "python"
    assert result[2]["content"] == "print('done')"
    # thinking/text parts carry exactly {"type", "content"} - no "language" key.
    assert set(result[0].keys()) == {"type", "content"}
    assert set(result[1].keys()) == {"type", "content"}
    assert set(result[2].keys()) == {"type", "language", "content"}


def test_fallback_reasoning_marker_format_is_recognized_as_thinking():
    # Proves the real api_provider.split_reasoning_and_content is genuinely
    # being called (a <think>-tag-only reimplementation would miss this
    # fallback marker format entirely).
    response = (
        "--- REASONING ---\n"
        "stepping through the fallback format\n"
        "--- END REASONING ---\n"
        "The final answer."
    )
    result = parse_response(response)
    assert result[0] == {"type": "thinking", "content": "stepping through the fallback format"}
    assert result[1] == {"type": "text", "content": "The final answer."}


def test_whitespace_only_around_otherwise_empty_content_returns_empty_list():
    # Mixed whitespace (tabs/newlines/spaces) with nothing else - must not
    # produce a spurious text part from the "if not parts and
    # response_text.strip()" fallback (that fallback only fires when the
    # raw response has non-whitespace content left after stripping).
    assert parse_response("\n\t  \r\n   \t\n") == []
