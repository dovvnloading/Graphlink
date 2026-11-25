"""Response-text parsing: reasoning/answer splitting for think-tag and
Harmony-format model output, plus response-field extraction helpers.

Function bodies are relocated VERBATIM from api_provider.py; only the
patch-seam rewrites below are new. Any name that lives in api_provider's
module namespace (module globals, sibling helpers, constants, and the
`ollama`/`urllib`/`requests` module bindings) is accessed late-bound as
`_mod.<name>` through an in-body deferred `import api_provider as _mod`,
NEVER via a module-top import here: a top-level `from api_provider import X`
would be a circular import (api_provider imports this module at ITS top)
AND would freeze the name at import time, making the test suite's
`monkeypatch.setattr(api_provider, "X", ...)` patches invisible to these
functions. The deferred-import-then-attribute pattern resolves the name on
api_provider at call time, so those patch seams keep working with zero test
changes. api_provider.py re-exports every name below, so every existing
`api_provider.<name>` caller and patch site is unchanged.
"""

from __future__ import annotations

import re


def _extract_response_field(payload, field_name: str, default=None):
    if payload is None:
        return default
    if isinstance(payload, dict):
        return payload.get(field_name, default)
    if hasattr(payload, field_name):
        return getattr(payload, field_name)
    try:
        return payload[field_name]
    except Exception:
        return default


def _append_unique_text_segment(parts: list[str], text, seen: set[str]):
    normalized = str(text or "").strip()
    if not normalized:
        return

    key = re.sub(r"\s+", " ", normalized).strip().lower()
    if not key or key in seen:
        return

    seen.add(key)
    parts.append(normalized)


def _strip_leading_harmony_tokens(text: str) -> str:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    remaining = str(text or "").lstrip()
    while True:
        marker = (
            _mod._HARMONY_FINAL_MARKER_PATTERN.match(remaining)
            or _mod._HARMONY_END_MARKER_PATTERN.match(remaining)
        )
        if marker is None:
            return remaining
        remaining = remaining[marker.end():].lstrip()


def _split_harmony_reasoning_block(text: str) -> tuple[str, str] | None:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    raw_text = str(text or "")
    prefix_match = _mod._HARMONY_ANALYSIS_PREFIX_PATTERN.match(raw_text)
    if not prefix_match:
        return None

    remaining = raw_text[prefix_match.end():]
    final_match = _mod._HARMONY_FINAL_MARKER_PATTERN.search(remaining)
    end_match = _mod._HARMONY_END_MARKER_PATTERN.search(remaining)

    if final_match and (not end_match or final_match.start() <= end_match.start()):
        reasoning_text = remaining[:final_match.start()].strip()
        answer_text = remaining[final_match.end():].strip()
        return reasoning_text, answer_text

    if end_match:
        reasoning_text = remaining[:end_match.start()].strip()
        answer_text = _mod._strip_leading_harmony_tokens(remaining[end_match.end():]).strip()
        return reasoning_text, answer_text

    return remaining.strip(), ""


def _split_closing_only_think_block(text: str) -> tuple[str, str] | None:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    raw_text = str(text or "")
    closing_match = _mod._THINK_CLOSING_ONLY_PATTERN.search(raw_text)
    if not closing_match:
        return None

    prefix = raw_text[:closing_match.start()]
    suffix = raw_text[closing_match.end():]
    # With no opening marker, this is inherently heuristic. Require a
    # standalone closing line after nonempty reasoning-like text. Prefer
    # preserving the answer if its prefix looks like quoted or example code.
    if not prefix.strip() or prefix.rsplit("\n", 1)[-1].strip() or suffix.split("\n", 1)[0].strip():
        return None
    if re.search(
        r"`|<(?:think|thinking|code_block)>|^[ \t]{0,3}(?:~{3,}|>)|^(?: {4}|\t)",
        prefix, re.IGNORECASE | re.MULTILINE,
    ):
        return None

    return prefix.strip(), suffix.strip()


def split_reasoning_and_content(text: str) -> tuple[str, str]:
    """Extract leading reasoning envelopes without scanning the final answer."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    remaining_text = str(text or "").strip()
    if not remaining_text:
        return "", ""

    reasoning_parts: list[str] = []
    reasoning_seen: set[str] = set()
    found_envelope = False

    while remaining_text:
        # Harmony explicitly establishes the final-answer boundary. Never
        # interpret protocol examples inside the returned answer as reasoning.
        harmony_split = _mod._split_harmony_reasoning_block(remaining_text)
        if harmony_split is not None:
            reasoning, answer = harmony_split
            _mod._append_unique_text_segment(reasoning_parts, reasoning, reasoning_seen)
            return "\n\n".join(reasoning_parts), answer

        answer = _mod._strip_leading_harmony_tokens(remaining_text)
        if answer != remaining_text:
            return "\n\n".join(reasoning_parts), answer.strip()

        # Consume only contiguous leading envelopes, retaining deduplication
        # for providers that emit more than one. Once visible text begins,
        # paired markers (including those in fenced/inline code) are literal.
        think_match = _mod._THINK_TAG_PATTERN.match(remaining_text)
        fallback_match = _mod._FALLBACK_REASONING_PATTERN.match(remaining_text) if not think_match else None
        if think_match:
            _mod._append_unique_text_segment(reasoning_parts, think_match.group(2), reasoning_seen)
            remaining_text = remaining_text[think_match.end():].strip()
        elif fallback_match:
            _mod._append_unique_text_segment(reasoning_parts, fallback_match.group(1), reasoning_seen)
            remaining_text = remaining_text[fallback_match.end():].strip()
        else:
            break
        found_envelope = True

    # Only try the ambiguous closing-only format on the original response,
    # never on text already established as the answer by a leading envelope.
    if not found_envelope:
        closing_only_split = _mod._split_closing_only_think_block(remaining_text)
        if closing_only_split is not None:
            return closing_only_split

    return "\n\n".join(reasoning_parts), remaining_text


class ReasoningWithoutAnswerError(RuntimeError):
    """A reasoning-capable model returned chain-of-thought text but no final answer.

    Distinct from a plain RuntimeError so callers (see the Ollama branch of chat())
    can specifically retry this failure mode - it's often just sampling variance (the
    model didn't finish "thinking" within its own budget that particular time), not a
    persistent configuration problem.
    """


def _compose_reasoned_response(answer_text: str, reasoning_text: str, provider_name: str) -> str:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    normalized_answer = str(answer_text or "").strip()
    normalized_reasoning = str(reasoning_text or "").strip()

    if normalized_answer:
        if normalized_reasoning:
            return f"<think>{normalized_reasoning}</think>\n{normalized_answer}"
        return normalized_answer

    if normalized_reasoning:
        raise _mod.ReasoningWithoutAnswerError(
            f"{provider_name} returned reasoning but no final answer. "
            "Retry in Quick mode or choose a different chat format/model."
        )

    raise RuntimeError(f"{provider_name} returned an empty response.")
