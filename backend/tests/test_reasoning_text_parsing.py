"""Regression coverage for literal reasoning markers in model answers (#1047)."""

import pytest

import api_provider
from backend.response_parsing import parse_response


@pytest.mark.parametrize("answer", [
    "```xml\n<think>example text</think>\n```",
    "Example:\n```xml\n<thinking>example text</thinking>\n```",
    "Use `<think>example</think>` in the template.",
    "Quote: \"<think>example</think>\".",
    "A literal <think>example</think> is valid XML.",
    "Use the </think> closing tag here.",
    "`</thinking>` is the closing tag.",
    "</think> is a closing tag.",
    "Example:\n```xml\n</think>\n```",
    "Example:\n~~~xml\n</thinking>\n~~~",
    "Example:\n    </think>\nMore text.",
    "Example:\n<code_block>\n</think>\n</code_block>",
    "Example:\n> </think>\nMore text.",
    "A literal marker:\n```text\n--- REASONING ---\nexample\n--- END REASONING ---\n```",
    "The format is --- REASONING --- example --- END REASONING ---.",
    "Example: `<|end|>` and `<|start|>assistant<|channel|>final<|message|>`.",
])
def test_literal_markers_remain_visible(answer):
    assert api_provider.split_reasoning_and_content(answer) == ("", answer)
    assert api_provider.split_reasoning_and_content(f"<think>actual reasoning</think>\n{answer}") == (
        "actual reasoning", answer,
    )


@pytest.mark.parametrize("prefix", [
    "<think>actual reasoning</think>",
    "<THINKING>actual reasoning</THINKING>",
    "--- REASONING ---\nactual reasoning\n--- END REASONING ---",
    "actual reasoning\n</think>",
    "<|channel|>analysis<|message|>actual reasoning<|start|>assistant<|channel|>final<|message|>",
    "<|channel|>analysis<|message|>actual reasoning<|end|><|start|>assistant<|channel|>final json<|message|>",
])
def test_leading_reasoning_keeps_the_literal_final_example(prefix):
    answer = "Here is the example:\n```xml\n<think>example</think>\n```"
    assert api_provider.split_reasoning_and_content(f"{prefix}\n{answer}") == ("actual reasoning", answer)


@pytest.mark.parametrize("prefix", [
    "<think>actual reasoning</think>",
    "--- REASONING ---actual reasoning--- END REASONING ---",
    "actual reasoning\n</thinking>",
    "<|channel|>analysis<|message|>actual reasoning",
    "<|channel|>analysis<|message|>actual reasoning<|end|>",
])
def test_reasoning_only_still_triggers_the_provider_retry_error(prefix):
    reasoning, answer = api_provider.split_reasoning_and_content(prefix)
    assert (reasoning, answer) == ("actual reasoning", "")
    with pytest.raises(api_provider.ReasoningWithoutAnswerError):
        api_provider._compose_reasoned_response(answer, reasoning, "test provider")


def test_consecutive_leading_envelopes_still_deduplicate_reasoning():
    reply = "<think>First</think>\n<thinking>first</thinking>\n--- REASONING ---Second--- END REASONING ---\nAnswer."
    assert api_provider.split_reasoning_and_content(reply) == ("First\n\nSecond", "Answer.")


def test_harmony_final_prefix_does_not_reparse_answer():
    answer = "Here: <think>example</think> and `<|end|>`."
    reply = f"<|end|><|start|>assistant<|channel|>final<|message|>{answer}"
    assert api_provider.split_reasoning_and_content(reply) == ("", answer)


def test_explicit_reasoning_does_not_reparse_unmatched_closing_marker():
    answer = "Use this closing delimiter:\n</think>\nThen continue."
    assert api_provider.split_reasoning_and_content(f"<think>actual reasoning</think>\n{answer}") == (
        "actual reasoning", answer,
    )


@pytest.mark.parametrize("reasoning", ["", "actual reasoning"])
def test_chat_parser_retains_the_xml_code_node(reasoning):
    answer = "Here is the example:\n```xml\n<think>example text</think>\n```"
    reply = api_provider._compose_reasoned_response(answer, reasoning, "test provider")
    expected = [{"type": "thinking", "content": reasoning}] if reasoning else []
    expected += [
        {"type": "text", "content": "Here is the example:"},
        {"type": "code", "language": "xml", "content": "<think>example text</think>"},
    ]
    assert parse_response(reply) == expected


def test_chat_parser_keeps_unmatched_literal_closing_tag_in_code_node():
    reply = "```xml\n</thinking>\n```"
    assert parse_response(reply) == [{"type": "code", "language": "xml", "content": "</thinking>"}]
