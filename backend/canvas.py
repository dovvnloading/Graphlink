"""Canvas orchestration + wire layer (Qt-removal plan R1; split at ADR-002
stage 2.2).

This module is now the ORCHESTRATION/WIRE/REGISTRATION half of the canvas:
register_canvas() wires every scene/grid topic and intent onto a SessionBus,
translating between the WS layer, the AgentDispatcher, and the pure scene
domain. The domain half - SceneDocument/SceneNode/SceneEdge, the content
codec, and every layout/appearance constant - was relocated VERBATIM to
backend/domain/ (graph.py / model.py / content_codec.py; each carries its
relocation note), whose purity is enforced permanently by
tests/test_domain_purity.py.

canvas.py imports every domain name back for its own use, so every existing
`from backend.canvas import X` consumer (session_load/session_save/plugins/
tests, incl. the private _content_codec namespace - the SAME shared
instance, identity preserved) keeps working unchanged. Wire-only helpers
(_research_result_wire, _history_turn_text, _history_token_text,
_chart_source_text, _format_branches_for_comparison, _placeholder_chart_data)
stay here.

ADR-002 stage 2.6 (PR1-3) relocated register_canvas's own former
~1570-line closure set, feature area by feature area, into
backend/api/intents_*.py (see that package's own docstring). register_canvas
is now a thin orchestrator: it constructs the shared SceneDocument,
registers the handful of topics with no natural feature home, then calls
each register_*_intents function in turn.
"""

from __future__ import annotations

from typing import Any

from graphlink_chart_data import SUPPORTED_CHART_TYPES

from backend.agents import AgentDispatcher
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.token_counter import TokenCounterState

# ADR-002 stage 2.2: the scene data model + content codec were relocated
# VERBATIM into backend/domain/ (model.py / content_codec.py - see their own
# docstrings). canvas.py imports every name back for its own use, so every
# existing `from backend.canvas import X` consumer (session_load/session_save/
# plugins/tests, incl. the private _content_codec namespace - the SAME shared
# instance, identity preserved) keeps working unchanged.
from backend.domain.content_codec import _content_codec
from backend.domain.graph import SceneDocument
from backend.domain.model import (
    BRANCH_HORIZONTAL_SPACING,
    CHART_MAX_HEIGHT,
    CHART_MAX_WIDTH,
    CHART_MIN_HEIGHT,
    CHART_MIN_WIDTH,
    CHAT_TITLE_PREVIEW_LENGTH,
    CODE_TITLE_PREVIEW_LENGTH,
    DRAG_FACTOR_MAX,
    DRAG_FACTOR_MIN,
    DRAG_PERCENT_MAX,
    DRAG_PERCENT_MIN,
    DRAG_PERCENT_PRESETS,
    FONT_COLOR_PRESETS,
    FONT_FAMILIES,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    GRID_COLOR_PRESETS,
    GROUP_COLLAPSED_HEIGHT,
    GROUP_COLLAPSED_WIDTH,
    GROUP_INELIGIBLE_FRAME_MEMBER_KINDS,
    GROUP_MEMBER_DEFAULT_HEIGHT,
    GROUP_MEMBER_DEFAULT_WIDTH,
    GROUP_PADDING,
    GROUP_PADDING_TOP,
    HTML_TITLE_PREVIEW_LENGTH,
    IMAGE_TITLE_PREVIEW_LENGTH,
    MESSAGE_VERTICAL_SPACING,
    NOTE_AGENT_BODY_COLOR,
    NOTE_AGENT_HEADER_COLOR,
    NOTE_AGENT_X_OFFSET,
    ORGANIZE_SPACING_X,
    ORGANIZE_SPACING_Y,
    SceneEdge,
    SceneEmptyPromptError,
    SceneError,
    SceneNode,
    THINKING_TITLE_PREVIEW_LENGTH,
)
from backend.domain.node_states import (
    ArtifactState,
    ChatState,
    CodeSandboxState,
    CodeState,
    DocumentState,
    GitlinkState,
    HtmlState,
    ImageState,
    PycoderState,
    WebResearchState,
)




def _research_result_wire(result) -> dict[str, Any]:
    """Camel-cases a ResearchResult (graphlink_plugins/web_research/domain.py)
    for the wire - a pure mapping function, NOT a SceneDocument method.
    Duck-typed on purpose: canvas.py imports nothing from
    graphlink_plugins.web_research (same posture as apply_web_research_progress
    above)."""
    return {
        "requestId": result.request_id,
        "originalQuery": result.original_query,
        "effectiveQuery": result.effective_query,
        "answerMarkdown": result.answer_markdown,
        "sources": [
            {
                "sourceId": s.source_id,
                "title": s.title,
                "url": s.url,
                "canonicalUrl": s.canonical_url,
                "snippet": s.snippet,
                "rank": s.rank,
                "provider": s.provider,
                "finalUrl": s.final_url,
                "status": s.status,
                "errorCode": s.error_code,
                "errorMessage": s.error_message,
                "truncated": s.truncated,
                "contentHash": s.content_hash,
                "citationCount": s.citation_count,
            }
            for s in result.sources
        ],
        "citations": [
            {"sourceId": c.source_id, "marker": c.marker, "claimContext": c.claim_context}
            for c in result.citations
        ],
        "warnings": list(result.warnings),
        "providerSnapshot": dict(result.provider_snapshot),
    }


def _history_turn_text(turn: dict) -> str:
    """Flattens one chat_branch_history entry's "content" into plain text,
    for callers whose interface is a flat string (token estimation here;
    _chart_source_text below has its own, separate flattening for
    ChartDataAgent). A turn's content is a plain str for an ordinary
    message, or (R8a attachments) a content_parts list of {"type", ...}
    dicts - only the "text" part has a token-count analog, so image/audio
    parts are skipped rather than stringified."""
    content = turn.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _history_token_text(history: list[dict]) -> str:
    """Joins an entire chat_branch_history's turns into one string for
    token_counter.py's set_context_text (which, like set_input_text, takes
    a single flat string and estimates it via the real tiktoken-backed
    estimator - ADR-016 stage 16.2)."""
    return "\n\n".join(text for text in (_history_turn_text(turn) for turn in history) if text)


def _chart_source_text(branch_history: list[dict]) -> str:
    """R6.2: flattens chat_branch_history's own {"role","content"} list (the
    SAME branch walk web_research/pycoder/gitlink already reuse via
    document.chat_branch_history - see this module's own docstring
    convention) into the single plain-text string ChartDataAgent.get_response
    expects. NOT a new branch-walking helper (chat_branch_history already IS
    that, reused as-is) - just the formatting step its list-of-dicts shape
    needs before it can be handed to an agent whose interface is a flat
    string, mirroring legacy graphlink_window_actions.py's own
    _build_chart_source_text/history_to_transcript role for the same
    call site. Empty/whitespace-only turns are skipped so an accidental blank
    ChatNode doesn't inject a stray blank line."""
    lines = []
    for turn in branch_history:
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        speaker = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")
    return "\n\n".join(lines)


def _format_branches_for_comparison(branches: list[tuple[str, list[dict]]]) -> str:
    """ADR-002 Workstream 1 ("Compare Branches"): flattens 2+ labeled
    chat_branch_history results into the single plain-text block
    BranchComparisonAgent.get_response expects - one labeled section per
    branch, each turn formatted "Speaker: text" (same convention as
    _chart_source_text above), but built on _history_turn_text rather than
    that function's own simpler `str(turn.get("content") or "")` - a turn
    carrying R8a attachment content_parts (a list, not a plain string)
    needs _history_turn_text's real flattening or it would stringify the
    raw list instead of extracting the text part."""
    sections = []
    for label, history in branches:
        lines = []
        for turn in history:
            text = _history_turn_text(turn).strip()
            if not text:
                continue
            speaker = "User" if turn.get("role") == "user" else "Assistant"
            lines.append(f"{speaker}: {text}")
        sections.append(f"=== {label} ===\n" + "\n\n".join(lines))
    return "\n\n".join(sections)


def _placeholder_chart_data(chart_type: str) -> dict[str, Any]:
    """R6.2: a minimal, already-canonical-SHAPED payload (matching
    canonicalize_chart_data's own output keys exactly) for the rare
    defensive case in generate_chart below where ChartDataAgent's response
    carries no top-level "error" key yet still fails
    canonicalize_chart_data - see that function's own call site for the full
    reasoning. One trivial data point per chart kind: just enough for
    render_chart_png to draw SOMETHING rather than leave the request a
    silent no-op, matching this feature's "never a blank/broken state"
    contract. NOT run back through canonicalize_chart_data itself - callers
    that need genuinely validated data should generate it properly instead."""
    if chart_type == "sankey":
        return {"type": "sankey", "title": "Chart", "flows": [{"source": "A", "target": "B", "value": 1.0}]}
    if chart_type == "histogram":
        return {
            "type": "histogram", "title": "Chart", "values": [0.0, 1.0], "bins": 2,
            "xAxis": "Value", "yAxis": "Frequency",
        }
    return {
        "type": chart_type,
        "title": "Chart",
        "labels": ["A", "B"],
        "values": [1.0, 1.0],
        "xAxis": "Category" if chart_type == "bar" else "Sequence",
        "yAxis": "Value",
    }


# ADR-002 stage 2.6: register_canvas's own body split into one
# register_*_intents(bus, document, ...) function per feature area, each
# relocated VERBATIM into backend/api/ - see that package's own docstring.
# Placed HERE (after the 6 helper functions above, not with this module's
# other imports at the top) because backend/api/intents_chat.py imports
# _history_token_text back from this module - a two-way relationship
# resolved by import ORDER: by the time Python reaches this line, every
# helper function above has already been assigned into this module's own
# namespace, so intents_chat.py's own `from backend.canvas import
# _history_token_text` (evaluated when the import below first pulls that
# module in) succeeds instead of hitting a partially-initialized module.
from backend.api.intents_artifact import register_artifact_intents  # noqa: E402
from backend.api.intents_branches import register_branches_intents  # noqa: E402
from backend.api.intents_chart import register_chart_intents  # noqa: E402
from backend.api.intents_chat import register_chat_intents  # noqa: E402
from backend.api.intents_chat_image import register_chat_image_intents  # noqa: E402
from backend.api.intents_code_sandbox import register_code_sandbox_intents  # noqa: E402
from backend.api.intents_conversation import register_conversation_intents  # noqa: E402
from backend.api.intents_gitlink import register_gitlink_intents  # noqa: E402
from backend.api.intents_grid import register_grid_intents  # noqa: E402
from backend.api.intents_groups import register_groups_intents  # noqa: E402
from backend.api.intents_nodes import register_node_intents  # noqa: E402
from backend.api.intents_pins import register_pins_intents  # noqa: E402
from backend.api.intents_pycoder import register_pycoder_intents  # noqa: E402
from backend.api.intents_view import register_view_intents  # noqa: E402
from backend.api.intents_undo import register_undo_intents  # noqa: E402
from backend.api.intents_web_research import register_web_research_intents  # noqa: E402


def register_canvas(
    bus: SessionBus,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
    composer_document: ComposerDocument,
    token_counter: TokenCounterState | None = None,
) -> SceneDocument:
    """Give a session its canvas document + the scene/grid topics and every
    R1 intent. Intent names for grid mirror GridControlBridge's @Slot names
    1:1 so the R2 island port is a transport swap, not a redesign.

    R4: agent_dispatcher/composer_document are threaded through so
    sendMessage's real Send action (below) can hand off to the real agent
    dispatch pipeline instead of the R3-era deferred notice. token_counter
    (R8a) lets sendMessage/regenerateResponse set real outputTokens/
    contextTokens once a reply completes - see those intents' own comments.
    Optional (default a throwaway, unregistered instance) only so the ~30
    pre-R8a canvas/agents tests that call register_canvas with 4 positional
    args keep working unchanged - the real (and only) production call site,
    backend/app.py's _configure_session, always passes the session's real,
    bus-registered one."""

    if token_counter is None:
        token_counter = TokenCounterState()

    document = SceneDocument()

    # ADR-003 stage 3.4: the ONE topic large enough to be worth a delta
    # protocol (a 500-node scene snapshot is ~1.6 MB; every other topic's
    # whole payload is smaller than the bookkeeping a delta would cost) -
    # see SessionBus.register_topic's own docstring.
    bus.register_topic(
        "scene",
        document.scene_payload,
        patch_builder=document.take_dirty_patch_ops,
        baseline_builder=document.published_scene_payload,
        # ADR-003 stage 3.5: the patch protocol (stage 3.4) is a real breaking
        # change for a reader that predates it - kind:"patch" previously hit
        # the transport's unknown-kind fallback and was silently dropped, so
        # a stale (not-yet-rebuilt) frontend bundle would keep subscribing
        # successfully and then simply never update again. schema_version=2
        # marks that; min_compatible=2 is what actually enforces it - it
        # tells a v1 reader it is too old, rather than leaving the version
        # number purely decorative (see WsTransport.onVersionRejection).
        schema_version=2,
        min_compatible=2,
    )
    bus.register_topic("grid-control", document.grid_payload)
    # Static preset topics, field-for-field the DragSpeedStatePayload /
    # FontControlStatePayload shapes so the generated validators apply
    # unchanged (same reuse as grid-control).
    bus.register_topic(
        "drag-speed",
        lambda: {
            "percentPresets": list(DRAG_PERCENT_PRESETS),
            "percentMin": DRAG_PERCENT_MIN,
            "percentMax": DRAG_PERCENT_MAX,
        },
    )
    bus.register_topic(
        "font-control",
        lambda: {
            "fontFamilies": list(FONT_FAMILIES),
            "colorPresets": list(FONT_COLOR_PRESETS),
            "sizeMin": FONT_SIZE_MIN,
            "sizeMax": FONT_SIZE_MAX,
        },
    )

    # publish_scene/publish_grid (former lines 299-303) and
    # publish_token_counter (R8a, former lines 305-310) all formerly lived
    # here too. Every one of their consumers has now relocated into
    # backend/api/ modules (ADR-002 stage 2.6, PR1-3), each of which gets
    # its own equivalent via backend/api/_shared.py's make_publish_scene/
    # make_publish_grid/make_publish_token_counter instead. Nothing in
    # register_canvas's own remaining body calls any of the three, so all
    # three were removed here rather than left as dead code.

    register_node_intents(bus, document, agent_dispatcher)
    register_conversation_intents(bus, document, notifications, agent_dispatcher)
    register_chat_intents(bus, document, notifications, agent_dispatcher, composer_document, token_counter)
    register_chat_image_intents(bus, document, notifications, agent_dispatcher)

    register_web_research_intents(bus, document, notifications, agent_dispatcher)
    register_artifact_intents(bus, document, notifications, agent_dispatcher)
    register_chart_intents(bus, document, notifications, agent_dispatcher)
    register_branches_intents(bus, document, notifications, agent_dispatcher, composer_document)
    register_gitlink_intents(bus, document, notifications, agent_dispatcher)
    register_pycoder_intents(bus, document, notifications, agent_dispatcher)
    register_code_sandbox_intents(bus, document, notifications, agent_dispatcher)

    register_groups_intents(bus, document)
    register_pins_intents(bus, document)
    register_view_intents(bus, document)
    # ADR-010 stage 10.2: undo/redo rides the scene topic (see that
    # module's own doc for why it is not its own topic).
    register_undo_intents(bus, document, notifications)
    register_grid_intents(bus, document)

    return document
