"""AgentRunOps - the SceneDocument methods for the three node kinds that
run an agent in the background (web research, artifact, code sandbox).

A MIXIN, composed exactly once, by backend/domain/graph.py's
SceneDocument. Method bodies are relocated VERBATIM from graph.py;
only the class wrapper, its docstring and the imports are new, and the
methods are regrouped by kind rather than left in the order successive
increments happened to append them in.

See backend/domain/nodes_code_review.py's docstring for why the
per-kind method groups are being lifted out of SceneDocument at all.
"""

from __future__ import annotations

import uuid

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import SceneError, SceneNode
from backend.domain.node_access import optional_node, require_node
from backend.domain.node_states import (
    ArtifactState,
    CodeSandboxState,
    WebResearchState,
)


class AgentRunOps(SceneDocumentParts):
    """Every SceneDocument method belonging to a node kind that runs an agent
    in the background: web research, artifact/drafter, and the Execution
    Sandbox.

    They are one group because they are one shape. Each has a creation
    primitive and then the same four-beat lifecycle - start the run, take
    progress, complete it, fail it - and each fails silently when its node
    is already gone, because a run that outlives the node the user deleted
    is not an error worth surfacing.
    """

    # -- R5.1: web research node ---------------------------------------------

    def add_web_research_node(self, x: float, y: float, parent_id: str | None) -> SceneNode:
        """The Web Research node's creation primitive - same required-parent
        posture as document/thinking/html/image/conversation nodes (never
        exists unparented). Title is always the fixed literal "Web Research"
        (mirrors conversation node's own fixed "Conversation" title - there
        is no meaningful single preview string before a query has ever been
        run). Content starts empty; the query text only lands once
        start_web_research_run is called."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Web Research",
            kind="web_research",
            state=WebResearchState(),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def start_web_research_run(self, node_id: str, query: str) -> SceneNode:
        """Begin one research run: stores the query text and resets this
        run's progress fields. Deliberately does NOT clear research_result -
        stale-while-revalidate: the previous run's answer stays visible until
        this run replaces it on success, or fails/cancels (leaving the stale
        result annotated by the new research_error)."""
        node = require_node(self.nodes, node_id, "web_research", WebResearchState)
        node.content = str(query)
        node.state.research_stage = ""
        node.state.research_completed = 0
        node.state.research_total = 0
        node.state.research_active_source_id = None
        node.state.research_error = ""
        return node

    def apply_web_research_progress(self, node_id: str, event) -> SceneNode | None:
        """Apply one duck-typed ProgressEvent-shaped update (.stage/.completed/
        .total/.source_id) - canvas.py deliberately does NOT import anything
        from graphlink_plugins.web_research (mirrors how
        start_conversation_reply's node param is duck-typed without
        agents.py importing backend.canvas.SceneNode). Silent no-op (returns
        None, never raises) if node_id is no longer in self.nodes - the node
        may have been deleted while a background run was still in flight."""
        node = optional_node(self.nodes, node_id, "web_research", WebResearchState)
        if node is None:
            return None
        node.state.research_stage = event.stage.value
        node.state.research_completed = event.completed
        node.state.research_total = event.total
        node.state.research_active_source_id = event.source_id
        return node

    def complete_web_research_run(self, node_id: str, result_wire: dict) -> SceneNode:
        """Land a successful run's result. Raises SceneError if the node is
        gone - the WS wrapper's own liveness check (in register_canvas)
        guards the actual mid-flight-delete race; this stays a hard
        precondition here, same posture as update_chat_node_content."""
        node = require_node(self.nodes, node_id, "web_research", WebResearchState)
        node.state.research_stage = "completed"
        node.state.research_error = ""
        node.state.research_active_source_id = None
        node.state.research_result = result_wire
        return node

    def fail_web_research_run(self, node_id: str, *, cancelled: bool, message: str) -> SceneNode:
        """Land a failed or cancelled run. research_result is deliberately
        left untouched (stale-while-revalidate - see start_web_research_run's
        own docstring)."""
        node = require_node(self.nodes, node_id, "web_research", WebResearchState)
        node.state.research_stage = "cancelled" if cancelled else "failed"
        node.state.research_error = message
        node.state.research_active_source_id = None
        return node

    def set_web_research_retain_to_knowledge(self, node_id: str, retain: bool) -> SceneNode:
        """ADR-021 stage 21.5: the per-node "keep these sources" preference.
        Same shape as set_code_sandbox_requirements below - validate the
        node exists and is the right kind, then write one state field."""
        node = require_node(self.nodes, node_id, "web_research", WebResearchState)
        node.state.research_retain_to_knowledge = bool(retain)
        return node

    # -- R5.2: artifact/drafter node -----------------------------------------

    def add_artifact_node(self, x: float, y: float, parent_id: str | None) -> SceneNode:
        """The Artifact/Drafter node's creation primitive - same required-
        parent posture as document/thinking/html/image/conversation/
        web_research nodes (never exists unparented). Title is always the
        fixed literal "Artifact" (mirrors conversation/web_research's own
        fixed titles - there is no meaningful single preview string before a
        document has ever been drafted). artifact_content starts empty; the
        document text only lands once complete_artifact_generation is
        called."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Artifact",
            kind="artifact",
            state=ArtifactState(),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def append_artifact_user_message(self, node_id: str, text: str) -> SceneNode:
        """Append a real user instruction to an artifact node's history -
        mirrors append_conversation_user_message exactly (same shape, same
        error-handling style)."""
        node = require_node(self.nodes, node_id, "artifact", ArtifactState)
        node.history.append({"role": "user", "content": str(text)})
        # A new instruction supersedes the previous failure - leaving the
        # banner up beside an in-flight run would report a stale outcome.
        node.state.artifact_error = ""
        return node

    def fail_artifact_generation(self, node_id: str, message: str) -> SceneNode | None:
        """Record a generation failure ON the node, so the card can say what
        went wrong instead of leaving a session-wide toast to be matched to
        one of several artifact nodes by guesswork.

        Returns None for a node that no longer exists rather than raising:
        this is called from the dispatch task's own except/timeout paths,
        where the node may well have been deleted mid-flight, and a failure
        report is not worth turning into a second failure."""
        node = optional_node(self.nodes, node_id, "artifact", ArtifactState)
        if node is None:
            return None
        node.state.artifact_error = str(message)
        return node

    def send_artifact_message(self, node_id: str, text: str) -> SceneNode:
        """The Artifact node's own Send action: a thin wrapper over
        append_artifact_user_message, kept as a separate method (rather than
        only calling append_artifact_user_message directly from the WS
        wrapper) so the WS intent name lines up 1:1 with the domain method,
        the same way send_conversation_message/append_conversation_user_message
        already do for ConversationNode."""
        return self.append_artifact_user_message(node_id, text)

    def complete_artifact_generation(self, node_id: str, new_content, ai_message: str) -> SceneNode:
        """Land a successful generation turn: WHOLE-DOCUMENT REPLACE (never an
        append/merge - the model returns the entire document every turn, see
        ArtifactState's own comment, backend/domain/node_states.py), plus
        append a real assistant turn to history. Raises SceneError if the node is
        gone - this WS wrapper does NOT pre-check liveness before calling
        this, same posture as send_conversation_message's own _on_reply, not
        web_research's more defensive pre-check pattern (there is no
        stage-stepper/persisted-error field here for a mid-flight delete to
        race against)."""
        node = require_node(self.nodes, node_id, "artifact", ArtifactState)
        node.state.artifact_content = str(new_content)
        node.state.artifact_error = ""
        node.history.append({"role": "assistant", "content": str(ai_message)})
        return node

    # -- R5.4: Execution Sandbox node ----------------------------------------
    #
    # Same import posture as every other plugin-backed kind's domain methods:
    # nothing here imports from graphlink_plugins.code_sandbox.

    def add_code_sandbox_node(self, x: float, y: float, parent_id: str | None) -> SceneNode:
        """The Virtual Environment Runner node's creation primitive - same
        required-parent posture as every R5 sibling. Title is always the
        fixed literal "Virtual Environment Runner" (matches
        backend/plugins.py's own plugin display name - renamed under
        ADR-002 P0 from "Execution Sandbox", which oversold what is
        actually a plain OS subprocess running inside a venv, not an
        OS-level sandbox; the internal kind="code_sandbox" identifier is
        UNCHANGED, since it's persisted wire/save-format state, not a
        display string). code_sandbox_sandbox_id is minted here, ONCE, at
        creation time - a short uuid4 hex used purely as this node's
        sandbox directory name (VirtualEnvSandbox re-sanitizes it again on
        its own side, but a short, already-safe id keeps the on-disk path
        short and human-scannable)."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Virtual Environment Runner",
            kind="code_sandbox",
            state=CodeSandboxState(code_sandbox_sandbox_id=uuid.uuid4().hex[:12]),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def set_code_sandbox_requirements(self, node_id: str, requirements_text: str) -> SceneNode:
        node = require_node(self.nodes, node_id, "code_sandbox", CodeSandboxState)
        node.state.code_sandbox_requirements = str(requirements_text)
        return node

    def set_code_sandbox_allow_source_builds(self, node_id: str, allow: bool) -> SceneNode:
        """ADR-005 stage 5.5: the approval panel's own source-build opt-in
        checkbox, fired on every toggle (same "ungated, fires immediately"
        posture as set_code_sandbox_requirements above). Setting this outside
        an open approval gate is harmless, not just permitted - agents.py
        resets the field to False at the top of every gate-open, so a value
        set here while no gate is open never reaches an actual run; the
        approval panel is the only surface that ever renders this control,
        and it only renders while awaiting_approval is true."""
        node = require_node(self.nodes, node_id, "code_sandbox", CodeSandboxState)
        node.state.code_sandbox_approval_allow_source_builds = bool(allow)
        return node

    def start_code_sandbox_run(self, node_id: str, input_text: str) -> SceneNode:
        """Begin one Run: stores input_text into code_sandbox_prompt and
        clears any previous error. Deliberately does NOT touch
        code_sandbox_code here - the dispatch
        method decides generate-vs-reuse by reading the EXISTING
        code_sandbox_code value at call time, so this must not overwrite it
        before that decision is made."""
        node = require_node(self.nodes, node_id, "code_sandbox", CodeSandboxState)
        node.state.code_sandbox_prompt = str(input_text)
        node.state.code_sandbox_error = ""
        return node

    def complete_code_sandbox_run(self, node_id: str, code: str, output: str, analysis: str) -> SceneNode | None:
        """Land a successful run. Execution Sandbox has no last_run_failed
        flag: an unrecovered failure after exhausting its own repair
        attempts surfaces as a failed run (see
        AgentDispatcher.start_code_sandbox_run), never as a "succeeded but
        flagged" result. Only ever reached via run_code_sandbox's own
        on_success closure (backend/api/intents_code_sandbox.py), whose
        node_id was already validated by start_code_sandbox_run's own guard
        earlier in the same request, so the kind check here is redundant on
        every live path - it is here so that a caller who gets it wrong gets
        None rather than a phantom code_sandbox_* attribute grafted onto
        some other kind's state (these are plain non-slotted dataclasses;
        the bad write would otherwise succeed silently)."""
        node = optional_node(self.nodes, node_id, "code_sandbox", CodeSandboxState)
        if node is None:
            return None
        node.state.code_sandbox_code = str(code)
        node.state.code_sandbox_output = str(output)
        node.state.code_sandbox_analysis = str(analysis)
        node.state.code_sandbox_awaiting_approval = False
        node.state.code_sandbox_approval_requirements = ""
        node.state.code_sandbox_approved_fingerprint = None
        node.state.code_sandbox_approval_allow_source_builds = False
        node.state.code_sandbox_approval_is_repair = False
        node.state.code_sandbox_error = ""
        return node

    def fail_code_sandbox_run(self, node_id: str, message: str) -> SceneNode | None:
        """Land a failed (or denied-approval, or cancelled) run - the
        awaiting_approval flag is ALWAYS cleared here too, unconditionally,
        so a denied/cancelled approval never leaves the node stuck showing
        the approval prompt forever (stale-while-revalidate: existing
        code/output/analysis survive untouched). Same kind-check posture as
        complete_code_sandbox_run - see its docstring."""
        node = optional_node(self.nodes, node_id, "code_sandbox", CodeSandboxState)
        if node is None:
            return None
        node.state.code_sandbox_awaiting_approval = False
        node.state.code_sandbox_approval_requirements = ""
        node.state.code_sandbox_approved_fingerprint = None
        node.state.code_sandbox_approval_allow_source_builds = False
        node.state.code_sandbox_approval_is_repair = False
        node.state.code_sandbox_error = str(message)
        return node
