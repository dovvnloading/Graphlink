"""ADR-010 close-out: the hand-authored undo/redo classification table.

Every intent the app registers (bus.register_intent, any topic) is listed
here EXACTLY ONCE, with a deliberate call: "A" (undoable - its handler wraps
its document mutation in CommandOps.record_command, so it lands on the
Ctrl+Z stack) or "B" (not undoable, WITH A REASON).

This table is not derived from the code - it is the actual decision a human
made about what SHOULD be undoable, entry by entry. test_undo_classification_
gate.py (same directory) cross-checks it against the real registered intents
AND against what the handlers actually do, in both directions:

  1. every intent bus.register_intent() actually registers must appear here
     (a newly-added intent with no entry fails the build - you must decide
     A or B, not silently inherit "not undoable" by omission);
  2. every entry here must still name a real, currently-registered intent
     (a removed/renamed intent leaves a stale entry - fails the build);
  3. every "A" entry's handler must actually call record_command somewhere
     in its reachable body (a claimed-A intent whose wrap was reverted or
     never written - fails the build, catches the claim going stale);
  4. every "B" entry's handler must NOT call record_command anywhere in its
     reachable body (a "B" intent that quietly grew a wrap - and so should
     have been reclassified "A" - fails the build too).

This is what closes the loop the user asked for: "no more wandering or
patchwork... all doors closed." A future PR that adds a new mutating intent
and forgets to either wrap it or explicitly mark it "B: <reason>" cannot
merge - CI fails, not "trust me, I checked."

Reason-category prefixes (informal, for fast scanning - not machine-checked):
  content         - a real document-content mutation; SHOULD be undoable (A)
  view-state      - zoom/scroll/splitter/per-node-scroll position
  preference      - appearance/behavior setting (grid, fonts, drag, routing,
                    reasoning level, model assignment, notification prefs)
  draft-state     - composer draft text/staged attachments before Send
  run-lifecycle   - start/progress/complete/fail/cancel of an agent run, or
                    a fetch/scan/cache of externally-sourced data
  read-only       - no document mutation at all
  security        - execution-approval / source-build-approval gate
  whole-session   - load/save/delete/rename/new chat session
  notification    - ephemeral UI banner, not document state
  undo-machinery  - the undo/redo intents themselves (self-referential)

See doc/adr/ADR-010-undo-redo-command-layer.md for the design; see
backend/domain/commands.py for record_command/CommandOps itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Classified:
    topic: str
    intent: str
    call: str  # "A" or "B"
    reason: str


CLASSIFICATION: tuple[Classified, ...] = (
    # -- backend/app.py (system) --------------------------------------------
    Classified("system", "ping", "B", "read-only: diagnostic echo, no document mutation"),

    # -- backend/notifications.py (notification) -----------------------------
    Classified("notification", "dismiss", "B", "notification: ephemeral UI banner, not document state"),
    Classified("notification", "showInfo", "B", "notification: ephemeral UI banner, not document state"),
    Classified("notification", "showError", "B", "notification: ephemeral UI banner, not document state"),

    # -- backend/composer.py (app-composer) ----------------------------------
    Classified("app-composer", "attachFile", "B", "draft-state: staged attachment before Send, not committed content"),
    Classified("app-composer", "removeAttachment", "B", "draft-state: staged attachment before Send, not committed content"),
    Classified("app-composer", "selectModel", "B", "preference: per-task model assignment"),
    Classified("app-composer", "updateDraft", "B", "draft-state: native-text-edit equivalent - the composer's own draft field before Send"),
    Classified("app-composer", "setReasoningLevel", "B", "preference: reasoning level, mirrors Settings' own reasoning-level setters"),

    # -- backend/agents.py (app-composer) ------------------------------------
    Classified("app-composer", "cancelChatRequest", "B", "run-lifecycle: cancel an in-flight generation"),

    # -- backend/chat_library.py (app-chat-library) --------------------------
    Classified("app-chat-library", "renameChat", "B", "whole-session: renames a saved session's title, not the live document"),
    Classified("app-chat-library", "deleteChat", "B", "whole-session: deletes a saved chat session record"),
    Classified("app-chat-library", "loadChat", "B", "whole-session: load - clear_for_load already clears command_log/redo_stack"),
    Classified("app-chat-library", "saveChat", "B", "whole-session: persistence, not an in-document edit"),
    Classified("app-chat-library", "newChat", "B", "whole-session: new - clear_for_load already clears command_log/redo_stack"),

    # -- backend/plugins.py (app-plugins) ------------------------------------
    Classified("app-plugins", "executePlugin", "A", "content: creates a new node (Web Research/Gitlink/PyCoder/Sandbox/Artifact/System-Prompt note/Conversation/HTML) - every branch wraps its own pluginX command_type"),

    # -- backend/api/intents_grid.py (grid-control) --------------------------
    Classified("grid-control", "setGridSize", "B", "preference: grid appearance"),
    Classified("grid-control", "setGridOpacityPercent", "B", "preference: grid appearance"),
    Classified("grid-control", "setGridStyle", "B", "preference: grid appearance"),
    Classified("grid-control", "setGridColor", "B", "preference: grid appearance"),

    # -- backend/api/intents_view.py (scene) ---------------------------------
    Classified("scene", "setSnapToGrid", "B", "preference: canvas behavior toggle"),
    Classified("scene", "setFadeConnections", "B", "preference: canvas appearance toggle"),
    Classified("scene", "setOrthogonalConnections", "B", "preference: connection routing style"),
    Classified("scene", "setSmartGuides", "B", "preference: canvas behavior toggle"),
    Classified("scene", "setDragFactor", "B", "preference: drag sensitivity"),
    Classified("scene", "setViewState", "B", "view-state: zoom/scroll position"),
    Classified("scene", "organizeNodes", "A", "content: auto-layout repositions every node - one Ctrl+Z restores them all"),
    Classified("scene", "setFontFamily", "B", "preference: canvas-appearance font"),
    Classified("scene", "setFontSize", "B", "preference: canvas-appearance font"),
    Classified("scene", "setFontColor", "B", "preference: canvas-appearance font"),

    # -- backend/api/intents_undo.py (scene) - the machinery itself ---------
    Classified("scene", "undo", "B", "undo-machinery: pops command_log itself, self-referential"),
    Classified("scene", "redo", "B", "undo-machinery: pops redo_stack itself, self-referential"),
    Classified("scene", "undoRun", "B", "undo-machinery: reverses a whole run via command_log, self-referential"),

    # -- backend/api/intents_web_research.py (scene) -------------------------
    Classified("scene", "runWebResearch", "B", "run-lifecycle: starts an agent run; node creation itself is executePlugin's job"),
    Classified("scene", "cancelWebResearchRequest", "B", "run-lifecycle: cancel"),

    # -- backend/api/intents_nodes.py (scene) --------------------------------
    Classified("scene", "addNode", "A", "content: create"),
    Classified("scene", "addChatNode", "A", "content: create"),
    Classified("scene", "addCodeNode", "A", "content: create"),
    Classified("scene", "addDocumentNode", "A", "content: create"),
    Classified("scene", "addThinkingNode", "A", "content: create"),
    Classified("scene", "addHtmlNode", "A", "content: create"),
    Classified("scene", "setHtmlSplitterState", "B", "view-state: per-node UI splitter position"),
    Classified("scene", "addImageNode", "A", "content: create"),
    Classified("scene", "addConversationNode", "A", "content: create"),
    Classified("scene", "moveNode", "A", "content: move"),
    Classified("scene", "moveNodes", "A", "content: move (bulk)"),
    Classified("scene", "removeNodes", "A", "content: delete"),
    Classified("scene", "connectNodes", "A", "content: connect"),
    Classified("scene", "removeEdges", "A", "content: delete edges"),

    # -- backend/api/intents_artifact.py (scene) -----------------------------
    Classified("scene", "sendArtifactMessage", "A", "content: user instruction + agent reply, both recorded"),
    Classified("scene", "cancelArtifactRequest", "B", "run-lifecycle: cancel"),

    # -- backend/api/intents_conversation.py (scene) -------------------------
    Classified("scene", "sendConversationMessage", "A", "content: user message"),
    Classified("scene", "appendConversationAssistantMessage", "A", "content: message append (agent reply or direct)"),
    Classified("scene", "deleteConversationMessage", "A", "content: sub-node message delete"),
    Classified("scene", "setNodeDocked", "A", "content: docked flag is document state"),
    Classified("scene", "deleteChatNode", "A", "content: delete"),
    Classified("scene", "setChatCollapsed", "A", "content: collapsed flag is document state"),
    Classified("scene", "setBranchStatus", "A", "content: branch status is document state"),
    Classified("scene", "setFinalDeliverable", "A", "content: final-deliverable flag is document state"),
    Classified("scene", "collapseBranch", "A", "content: collapsed flag is document state"),
    Classified("scene", "collapseAllNodes", "A", "content: bulk collapsed-flag change, one composite undo"),
    Classified("scene", "expandAllNodes", "A", "content: bulk collapsed-flag change, one composite undo"),
    Classified("scene", "setChatScrollValue", "B", "view-state: per-node scroll position"),

    # -- backend/api/intents_gitlink.py (scene) ------------------------------
    Classified("scene", "fetchGitlinkRepositories", "B", "read-only: fetches provider repo list for display"),
    Classified("scene", "loadGitlinkRepoTree", "B", "run-lifecycle: caches a fetched repo-tree listing"),
    Classified("scene", "setGitlinkLocalRoot", "A", "content: user-typed/picked local folder path"),
    Classified("scene", "pickGitlinkLocalRoot", "A", "content: same field via the native folder picker"),
    Classified("scene", "importGitlinkSnapshot", "B", "run-lifecycle: caches an imported snapshot root from disk"),
    Classified("scene", "buildGitlinkContext", "B", "run-lifecycle: caches a built context artifact for review"),
    Classified("scene", "fetchGitlinkContext", "B", "read-only: fetches the built context XML, no mutation"),
    Classified("scene", "runGitlinkChangeSet", "B", "run-lifecycle: start/complete/fail an agent run"),
    Classified("scene", "cancelGitlinkRequest", "B", "run-lifecycle: cancel"),
    Classified("scene", "applyGitlinkChanges", "B", "run-lifecycle: writes real files to disk, an external side effect Ctrl+Z cannot safely reverse"),

    # -- backend/api/intents_groups.py (scene) -------------------------------
    Classified("scene", "addNote", "A", "content: create"),
    Classified("scene", "setNoteContent", "A", "content: text edit"),
    Classified("scene", "createFrame", "A", "content: create/group"),
    Classified("scene", "createContainer", "A", "content: create/group"),
    Classified("scene", "setGroupLabel", "A", "content: label edit"),
    Classified("scene", "setGroupColor", "A", "content: color edit"),
    Classified("scene", "toggleFrameLock", "A", "content: lock flag is document state (snapshot/restore, not replay - safe to flip)"),
    Classified("scene", "toggleGroupCollapsed", "A", "content: collapsed flag is document state"),
    Classified("scene", "resizeFrame", "A", "content: size is document state"),
    Classified("scene", "fitFrameToContent", "A", "content: size is document state"),
    Classified("scene", "ungroup", "A", "content: delete grouping"),

    # -- backend/api/intents_model_routing.py (scene) - ADR-018 stage 18.3 --
    Classified("scene", "setModelOverride", "A", "content: model pin is document state, same posture as setGroupColor"),
    Classified("scene", "clearModelOverride", "A", "content: model pin is document state, same posture as setGroupColor"),

    # -- backend/api/intents_pins.py (scene) ---------------------------------
    Classified("scene", "addPin", "A", "content: user-placed navigation waypoint"),
    Classified("scene", "movePin", "A", "content: user-placed navigation waypoint"),
    Classified("scene", "removePin", "A", "content: user-placed navigation waypoint"),
    Classified("scene", "updatePin", "A", "content: user-placed navigation waypoint"),

    # -- backend/api/intents_chart.py (scene) --------------------------------
    Classified("scene", "generateChart", "A", "content: agent-produced chart node"),
    Classified("scene", "resizeChart", "A", "content: size is document state"),
    Classified("scene", "toggleChartAspectLock", "A", "content: lock flag is document state (snapshot/restore, safe to flip)"),

    # -- backend/api/intents_chat.py (scene) ---------------------------------
    Classified("scene", "sendMessage", "A", "content: user message (hidden create - internally adds a chat node)"),
    Classified("scene", "regenerateResponse", "A", "content: delete-then-recreate of a node's content, one composite undo"),
    Classified("scene", "cancelChatRequest", "B", "run-lifecycle: cancel"),

    # -- backend/api/intents_chat_image.py (scene) ---------------------------
    Classified("scene", "generateImage", "A", "content: mints an image node + asset bytes via the shared _dispatch_image path"),
    Classified("scene", "regenerateImage", "A", "content: mints an image node + asset bytes via the shared _dispatch_image path"),

    # -- backend/api/intents_branches.py (scene) -----------------------------
    Classified("scene", "generateKeyTakeaway", "A", "content: agent-produced note via the shared _generate_note_from_node path"),
    Classified("scene", "generateExplainerNote", "A", "content: agent-produced note via the shared _generate_note_from_node path"),
    Classified("scene", "compareBranches", "A", "content: agent-produced comparison note"),
    Classified("scene", "synthesizeBranches", "A", "content: agent-produced synthesis chat node"),

    # -- backend/api/intents_code_sandbox.py (scene) -------------------------
    Classified("scene", "setCodeSandboxRequirements", "A", "content: requirements text is document state"),
    Classified("scene", "setCodeSandboxAllowSourceBuilds", "B", "security: source-build approval gate"),
    Classified("scene", "runCodeSandbox", "B", "run-lifecycle: start/complete/fail an agent run"),
    Classified("scene", "cancelCodeSandboxRequest", "B", "run-lifecycle: cancel"),

    # -- backend/api/intents_pycoder.py (scene) ------------------------------
    Classified("scene", "setPyCoderMode", "A", "content: mode is document state"),
    Classified("scene", "runPyCoder", "B", "run-lifecycle: start/complete/fail an agent run"),
    Classified("scene", "cancelPyCoderRequest", "B", "run-lifecycle: cancel"),
    Classified("scene", "approveCodeExecution", "B", "security: code-execution approval gate"),
    Classified("scene", "denyCodeExecution", "B", "security: code-execution approval gate"),

    # -- backend/api/intents_builder.py (builder + scene) --------------------
    # ADR-008 stage 8.3. Run-lifecycle intents are B exactly like runPyCoder:
    # the CONTENT a build produces is undoable through its own
    # run_id-stamped commands (and reversible wholesale via scene/undoRun);
    # starting/steering/stopping the run is not itself a document mutation.
    Classified("builder", "start", "A", "content: creates the plan node (the run it then starts is separate lifecycle; the build's own mutations are run_id-stamped commands)"),
    Classified("builder", "startExecution", "B", "run-lifecycle: start/resume the build's executor run"),
    Classified("builder", "cancel", "B", "run-lifecycle: cancel"),
    Classified("builder", "approveTool", "B", "security: builder tool-call approval gate"),
    Classified("builder", "denyTool", "B", "security: builder tool-call approval gate"),
    Classified("builder", "listRecipes", "B", "read-only: returns the recipe list"),
    Classified("builder", "saveRecipe", "B", "preference: writes the settings store's recipe list, not document state"),
    Classified("scene", "setPlanSteps", "A", "content: the plan checklist is document state"),

    # -- backend/api/intents_settings_general.py (app-settings) -------------
    Classified("app-settings", "setActiveSection", "B", "preference: which Settings page is open"),
    Classified("app-settings", "setShowTokenCounter", "B", "preference: appearance toggle"),
    Classified("app-settings", "setEnableSystemPrompt", "B", "preference: behavior toggle"),
    Classified("app-settings", "setNotificationPreference", "B", "preference: notification-type toggle"),
    Classified("app-settings", "setGithubToken", "B", "preference: write-only credential field, not document content"),
    Classified("app-settings", "clearGithubToken", "B", "preference: write-only credential field, not document content"),
    # ADR-006 stage 6.5:
    Classified("app-settings", "setProviderMode", "B", "preference: which provider mode is live/persisted"),
    # ADR-016 stage 16.1:
    Classified("app-settings", "setLogLevel", "B", "preference: local logging verbosity"),
    # ADR-018 stage 18.4:
    Classified("app-settings", "setAutoModelPolicy", "B", "preference: auto-routing policy, not document content"),
    # ADR-012 stage 12.2:
    Classified("app-settings", "setTheme", "B", "preference: local theme choice, not document content"),

    # -- backend/api/intents_settings_api_provider.py (app-settings) --------
    Classified("app-settings", "setViewingApiProvider", "B", "preference: which provider sub-page is viewed"),
    Classified("app-settings", "loadApiModels", "B", "run-lifecycle: fetches a live model catalog"),
    Classified("app-settings", "saveApiConfiguration", "B", "preference: API provider/key/model configuration"),
    Classified("app-settings", "resetApiSettings", "B", "preference: API provider/key/model configuration"),

    # -- backend/api/intents_settings_ollama.py (app-settings) --------------
    Classified("app-settings", "setOllamaReasoningLevel", "B", "preference: reasoning level"),
    Classified("app-settings", "setOllamaModelAssignment", "B", "preference: per-task model assignment"),
    Classified("app-settings", "scanOllamaSystem", "B", "run-lifecycle: scans/caches locally installed models"),
    Classified("app-settings", "pullOllamaModel", "B", "run-lifecycle: downloads a model"),
    Classified("app-settings", "pickOllamaScanFolder", "B", "run-lifecycle: native folder pick + scan"),

    # -- backend/api/intents_settings_llama_cpp.py (app-settings) -----------
    Classified("app-settings", "setLlamaCppReasoningLevel", "B", "preference: reasoning level"),
    Classified("app-settings", "setLlamaCppChatFormat", "B", "preference: runtime tunable"),
    Classified("app-settings", "setLlamaCppNCtx", "B", "preference: runtime tunable"),
    Classified("app-settings", "setLlamaCppNGpuLayers", "B", "preference: runtime tunable"),
    Classified("app-settings", "setLlamaCppNThreads", "B", "preference: runtime tunable"),
    Classified("app-settings", "pickLlamaCppChatModelFile", "B", "preference: staged model file path, not saved until saveLlamaCppSettings"),
    Classified("app-settings", "pickLlamaCppTitleModelFile", "B", "preference: staged model file path, not saved until saveLlamaCppSettings"),
    Classified("app-settings", "setLlamaCppChatModelPath", "B", "preference: staged model file path, not saved until saveLlamaCppSettings"),
    Classified("app-settings", "setLlamaCppTitleModelPath", "B", "preference: staged model file path, not saved until saveLlamaCppSettings"),
    Classified("app-settings", "scanLlamaCppSystem", "B", "run-lifecycle: scans/caches locally installed models"),
    Classified("app-settings", "pickLlamaCppScanFolder", "B", "run-lifecycle: native folder pick + scan"),
    Classified("app-settings", "saveLlamaCppSettings", "B", "preference: runtime configuration"),

    # -- backend/api/intents_diagnostics.py (diagnostics) --------------------
    # ADR-016 stage 16.4:
    Classified("diagnostics", "exportDiagnosticBundle", "B", "read-only: assembles a redacted diagnostic snapshot, no document mutation"),
    Classified("diagnostics", "openLogFolder", "B", "read-only: opens the OS file browser, no document mutation"),

    # -- backend/api/intents_knowledge.py (knowledge, scene) - ADR-017 stage 17.5 --
    Classified("knowledge", "search", "B", "read-only: queries the local knowledge store, no document mutation"),
    Classified("scene", "setChatIndexIntoKnowledge", "A", "content: branch-indexing opt-in is document state, same posture as setGroupColor"),
)
