# Artifact / Drafter — end-to-end redesign

Status: implementation roadmap; phase 0 in PR #458. This is a plan, not a claim
that the future phases are implemented. Preserve the existing `artifact` kind,
serialized state, and graph undo behavior throughout migration.

## Architecture and actual boundaries

The plugin manifest (`plugins/artifact`) only registers the picker item.
The real execution path is:

1. `ArtifactNodeView` sends `sendArtifactMessage` through `SceneStore`.
2. `backend/api/intents_artifact.py` appends the instruction to the node and
   passes graph-branch + node history to `AgentDispatcher.start_artifact_reply`.
3. `backend/agent_dispatch/research.py` claims an independent run, starts a
   worker, enforces timeout/cancel, and invokes `graphlink_artifact_agent.py`.
4. `ArtifactAgent` sends the entire current document and history through a
   model call and extracts a full Markdown replacement from `<artifact>` tags.
5. An async callback commits the replacement through
   `SceneDocument.complete_artifact_generation` and publishes a scene update.
6. Graph snapshots/patches and `ArtifactNodeView` display the new document.

This architecture has separate trust boundaries at each step: untrusted
document text, model-generated replacement text, late asynchronous callbacks,
WebSocket inputs, state mutation/undo, and rendering Markdown. A plugin-only
code cleanup does not touch most of them.

## Problem register and priority

| Priority | Problem | Impact | First response |
| --- | --- | --- | --- |
| P0 | Model response replaces document without checking its input revision | Human edits or undo can be overwritten by late generation | Compare draft + history snapshots before commit; explicit conflict |
| P0 | Concurrent Send modifies history before dispatcher refuses duplicate run | User instructions enter history without being processed | Claim before mutating and before first await |
| P0 | Document contents injected into a system message | Untrusted Markdown can masquerade as model policy | System policy separate; encoded document in user-data message |
| P0 | Read-only preview with no direct Markdown editing | AI is the only practical way to change text | Local editor + undoable, conflict-checked backend save |
| P1 | Full-document output required for every minor edit | Latency, token cost, truncation, failure rate grow with document size | Structured, checked section patches; full rewrite fallback |
| P1 | No explicit durable per-artifact revision timeline | Hard to compare, revert, or investigate lost changes after reload | Content-addressed snapshots and audit metadata |
| P1 | Weak long-document context strategy | Context overflow and misleading partial rewrites | Token budget, relevant-section selection, bounded output validation |
| P1 | No export beyond basic Markdown | Hard to move finalized work into other apps | Markdown now; PDF/DOCX/HTML via explicit export pipeline later |
| P2 | Editing card is a small virtualized node | Long-form editing and unsaved-state recovery are awkward | Dedicated document workspace, full-screen editor, draft recovery |
| P2 | Limited diagnostics/benchmarks for drafts | Regressions in large content and concurrent workflows are hard to see | Cost, latency, acceptance/rejection and checksum telemetry |

## Phase 0 — integrity and author control (PR #458)

- Keep current node kind and wire payload stable.
- Make original document lower-trust input, not a system instruction.
- Use fail-closed size limits rather than partial/truncated document writes.
- Reject a late generation if the node, source document, or instruction history
  no longer matches what that run read.
- Reject overlapping Send before adding an instruction to saved history.
- Provide human Markdown editing with a content precondition, undo/redo,
  explicit save acknowledgment, and plain Markdown download.
- Show saved-draft generation failures immediately in the card.
- Add deterministic regression tests for these boundaries.

This protects the whole-document architecture; it does not solve its scaling
limit or provide a durable document revision database.

## Phase 1 — checked incremental editing and diff review

Define a versioned edit protocol independent of any one provider:

```json
{
  "base_version": "opaque-revision",
  "operations": [
    {
      "kind": "replace-section",
      "section_id": "stable-heading-or-paragraph-id",
      "expected_sha256": "hash-of-original-section",
      "replacement_markdown": "..."
    }
  ],
  "summary": "What changed"
}
```

The server must validate a schema, maximum operation count, exact base
revision, target section identity/hash, range boundaries and maximum resulting
document length **before touching state**. Apply to a copy; publish a
human-readable diff preview; commit all operations atomically on explicit
acceptance. Failed operations must leave the draft unchanged. Never interpret
model output as executable code, a filesystem path, or a shell patch.

For small documents, retain full replacement mode as a fallback. For large
documents, retrieve only the sections and neighboring context relevant to
the instruction, plus an outline and explicit global constraints. This can
reduce outbound and generated text by an order of magnitude for local edits.
Do not claim any reduction until benchmarked against representative tasks.

Acceptance tests: section rename/move, duplicate headings, CRLF/Unicode,
code fences containing delimiters, stale revisions, conflicting edits,
malformed JSON, truncated responses and deliberately malicious model output.
No automatic overwrite on conflict.

## Phase 2 — durable revision timeline

Introduce per-artifact monotonic version numbers and content hashes, stored
atomically with user/agent provenance and timestamps. Prefer content-addressed
snapshots/checkpoints plus deltas over storing every full document for every
keystroke. Plan for migrations that load old sessions into a baseline
revision without changing their rendered content. Keep graph-level Undo/Redo
compatible, but distinguish an in-session command undo from a persisted
revision restore.

The editor should show "Draft version N", comparison between selected
versions, restore with confirmation, and conflict resolution (rebase, open
current version, copy/export unsaved text). Avoid storing raw private document
text in logs or external telemetry.

## Phase 3 — genuine document workspace

Move long-form editing out of the height-limited virtualized canvas card.
The node becomes an overview/launcher, while a dedicated workspace provides
source/rendered split view, headings outline, search/replace, keyboard-first
controls, autosaved *local unsent draft* recovery, tracked AI suggestion
diffs, document word/character stats and explicit export. Keep raw HTML
inert: use the existing safe Markdown renderer. Export via separately
reviewed text/PDF/DOCX converters, never by executing the document or
embedding untrusted active HTML.

Critical invariant: virtualization, reload and slow WebSocket acknowledgments
must not silently discard an unsaved user draft. Document buffers require
stable ownership outside the ephemeral rendered node component.

## Phase 4 — evaluation, observability and rollout

Build an offline deterministic eval suite:

- 1K, 10K, 100K and near-limit character documents, with local and global edits
- concurrent manual edit vs in-flight model, undo/redo, cancelled requests,
  disconnected clients, deletions/recreated IDs, and multi-tab stale writes
- adversarial Markdown instructions, quoted `<artifact>` delimiters,
  very large responses, unsafe HTML, and Unicode/binary boundaries
- structured patch accuracy against expected document sections; 0 silent
  overwrite or invalid partial commit
- measured tokens sent, generated tokens, wall-clock latency, provider cost,
  diff acceptance and exact-content preservation

Telemetry must not include document bodies, credentials, or full prompts.
Use aggregate timings and counters only. Set explicit thresholds after a
baseline run, not aspirational numbers mistaken for observed results.

## Release discipline

Keep work in sequential, non-overlapping PRs. For every phase: reviewed
architecture contract, targeted tests, Windows pytest/Ruff/mypy/coverage/perf
and dependency audit, frontend type checks, E2E, packaging, Linux permissions,
and final GitHub mergeability against latest `main`. No main-branch merge
until CI green. Delete merged branches to avoid the earlier conflict buildup.
