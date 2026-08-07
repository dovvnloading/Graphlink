"""ADR-006 stage 6.7: prompt-registry golden ratchet.

Every live prompt string in the codebase is registered in
graphlink_prompts.PROMPT_REGISTRY with a version and the sha256 of its
canonical text. These tests hold two invariants (same ratchet posture as
test_packaging_py_modules.py):

1. Editing any registered prompt WITHOUT bumping its version and updating
   its hash fails here, with an actionable message - prompt changes are
   always deliberate and reviewable, never silent drive-by edits.
2. The registry covers exactly the pinned id list below - deleting a prompt
   (or adding a new one without registering it) is a deliberate act that
   updates BOTH the registry and this pin.
"""

from __future__ import annotations

import graphlink_prompts

# The pinned prompt inventory. Adding a prompt to the codebase means adding
# it to PROMPT_REGISTRY (with a resolver + version 1 hash) AND to this pin;
# deleting one means removing it from both. Neither happens by accident.
EXPECTED_PROMPT_IDS = frozenset(
    {
        "chat-system-core",
        "chart-output-hard-rules",
        "chart-schema-templates",
        "note-key-takeaway",
        "note-branch-comparison",
        "note-branch-synthesis",
        "note-explainer",
        "web-research-query",
        "web-research-validation",
        "web-research-summary",
        "pycoder-execution",
        "pycoder-repair",
        "pycoder-repair-retry",
        "pycoder-analysis",
        "code-sandbox-generation",
        "code-sandbox-repair",
        "gitlink-system",
        "reasoning-hint-low",
        "reasoning-hint-high",
    }
)


def test_registry_covers_exactly_the_pinned_prompt_inventory():
    registered = set(graphlink_prompts.PROMPT_REGISTRY)
    missing = EXPECTED_PROMPT_IDS - registered
    extra = registered - EXPECTED_PROMPT_IDS
    assert not missing and not extra, (
        f"PROMPT_REGISTRY drifted from the pinned inventory. "
        f"Missing from registry: {sorted(missing)}; unpinned extras: "
        f"{sorted(extra)}. If this change is deliberate, update BOTH "
        "graphlink_prompts.PROMPT_REGISTRY and EXPECTED_PROMPT_IDS in "
        "this test."
    )


def test_registry_entries_are_self_consistent():
    for prompt_id, entry in graphlink_prompts.PROMPT_REGISTRY.items():
        assert entry.prompt_id == prompt_id
        assert entry.version >= 1
        assert len(entry.sha256) == 64


def test_every_registered_prompt_hash_matches_its_live_text():
    for prompt_id, entry in graphlink_prompts.PROMPT_REGISTRY.items():
        live_text = graphlink_prompts.resolve_prompt_text(prompt_id)
        assert isinstance(live_text, str) and live_text.strip(), (
            f"prompt {prompt_id!r} resolved to empty/non-string text"
        )
        actual = graphlink_prompts._sha256_text(live_text)
        assert actual == entry.sha256, (
            f"prompt {prompt_id!r} (version {entry.version}) was edited "
            "without updating its registry entry. If the edit is "
            "deliberate: bump the version in graphlink_prompts."
            f"PROMPT_REGISTRY and set sha256 to {actual!r} (or run: "
            'python -c "import graphlink_prompts as p; '
            f"print(p._sha256_text(p.resolve_prompt_text('{prompt_id}')))\")."
        )


def test_resolve_prompt_text_rejects_unknown_ids():
    import pytest

    with pytest.raises(KeyError):
        graphlink_prompts.resolve_prompt_text("no-such-prompt")
