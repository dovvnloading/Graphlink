"""Shared helpers for plugins that ask an LLM for structured JSON.

Extracted from independently hand-rolled, near-identical code in
graphlink_plugin_code_review.py, graphlink_plugin_quality_gate.py,
graphlink_plugin_workflow.py, and graphlink_plugin_gitlink.py: all four independently
implemented the exact same regex-based JSON-fence-stripping logic.

This is deliberately a pair of plain functions rather than a shared base class with a
get_response() template method. The three plugins that also share the "call the LLM,
then parse JSON" shape turned out to differ in exactly where fallback/normalization
happens relative to the try/except (see call_llm_and_parse_json's docstring) - forcing
that into one template method would have meant parameterizing around the difference or
risking a subtle behavior change, so each plugin keeps its own try/except/fallback
control flow and just calls these functions instead of duplicating their bodies.
"""

import json
import re

import api_provider


def extract_json_object(raw_text):
    """Pull a JSON object out of an LLM response that may be wrapped in a markdown
    fence, or preceded/followed by commentary. Falls back to the raw text if no JSON
    object shape is found (leaving it to the caller's json.loads to fail informatively).
    """
    block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw_text, re.IGNORECASE)
    if block_match:
        return block_match.group(1).strip()

    json_match = re.search(r"(\{[\s\S]*\})", raw_text)
    if json_match:
        return json_match.group(1).strip()

    return raw_text.strip()


def call_llm_and_parse_json(system_prompt, user_prompt, *, task):
    """Send a system/user prompt pair to the LLM and parse the response as JSON.

    Raises whatever api_provider.chat, json.loads, or extract_json_object raise -
    callers are expected to wrap this in their own try/except, since each plugin has a
    different fallback/normalization shape around the failure case (some fall back
    inside the same try block that also covers normalization, some outside it).
    """
    response = api_provider.chat(
        task=task,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw_text = response["message"]["content"]
    return json.loads(extract_json_object(raw_text))
