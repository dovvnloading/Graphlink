/**
 * Scene-topic client store (Qt-removal plan R1).
 *
 * Binds the WS transport's "scene" topic to a validated, subscribable local
 * snapshot, and exposes the intent surface backend/canvas.py registers.
 * Deliberately framework-free (plain listeners, no React import) so the
 * store logic is unit-testable without rendering; React consumes it through
 * useSyncExternalStore in SceneCanvas.
 */

import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { SceneState } from "../../lib/bridge-core/generated/scene-state";
import type { GridControlState } from "../../lib/bridge-core/generated/grid-control-state";
import type { DragSpeedState } from "../../lib/bridge-core/generated/drag-speed-state";
import type { FontControlState } from "../../lib/bridge-core/generated/font-control-state";
import type { StreamListener, WsTransport } from "../../lib/ws/transport";

export const initialSceneState: SceneState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  nodes: [],
  edges: [],
  pins: [],
  snapToGrid: false,
  fadeConnectionsEnabled: false,
  orthogonalRouting: false,
  smartGuides: false,
  hasSavedChat: false,
  dragFactor: 1,
  fontFamily: "Segoe UI",
  fontSizePt: 9,
  fontColor: "#F0F0F0",
};

export const initialDragSpeedState: DragSpeedState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  percentPresets: [25, 50, 75, 100],
  percentMin: 5,
  percentMax: 100,
};

export const initialFontControlState: FontControlState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  fontFamilies: ["Segoe UI"],
  colorPresets: [],
  sizeMin: 8,
  sizeMax: 16,
};

export const initialGridState: GridControlState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  gridSize: 10,
  gridOpacityPercent: 30,
  gridStyle: "Dots",
  gridColor: "#555555",
  sizePresets: [10, 20, 50, 100],
  stylePresets: ["Dots", "Lines", "Cross"],
  colorPresets: [],
};

type Listener = () => void;

export class SceneStore {
  private scene: SceneState = initialSceneState;
  private grid: GridControlState = initialGridState;
  private dragConfig: DragSpeedState = initialDragSpeedState;
  private fontConfig: FontControlState = initialFontControlState;
  private readonly listeners = new Set<Listener>();
  private readonly unsubscribers: Array<() => void> = [];
  // R5.1: the currently-selected canvas node id, mirrored from React Flow's
  // own onSelectionChange (see SceneCanvas.tsx) - local UI state only, never
  // sent over the wire on its own. Exists so PluginPicker can attach "which
  // node was selected when a plugin was launched" to executePlugin without
  // either component reaching into the other's internals.
  private selectedNodeId: string | null = null;
  // ADR-002 Workstream 1 ("Branch from here"): which chat node the NEXT
  // sendMessage should reply to instead of the current branch tip - local
  // UI state only, same posture as selectedNodeId above. Set by a chat
  // node's own context-menu action (ChatNodeView.tsx, wired in
  // toFlowNodes below), read by Composer.tsx to show a "Replying to"
  // indicator, and consumed-then-cleared by sendMessage itself so it never
  // silently applies to a later, unrelated send.
  private replyTargetNodeId: string | null = null;
  // ADR-002 Workstream 1 ("Synthesize Branches"): which 2+ chat nodes the
  // NEXT sendMessage should synthesize instead of sending an ordinary
  // reply - the list-valued sibling of replyTargetNodeId above, same local-
  // UI-state-only posture. Set by App.tsx's Synthesize Branches shortcut
  // (which already gathers React Flow's own multi-selection, the same
  // mechanism compareBranches below uses), read by Composer.tsx to show a
  // "Synthesizing N branches" indicator, and consumed-then-cleared by
  // sendMessage itself so it never silently applies to a later, unrelated
  // send. Mutually exclusive with replyTargetNodeId - setting one clears
  // the other (see both setters below) since a send cannot simultaneously
  // be "a reply to X" and "a synthesis of X, Y" - showing both indicators
  // at once would be confusing and only one intent can actually fire.
  private synthesizeTargetNodeIds: string[] | null = null;
  // ADR-002 Workstream 1 ("Branch status and lifecycle"): "reduce a complex
  // graph to its accepted paths" - a view-only review lens, NOT persisted
  // scene state (same "local UI state only" posture as branchFocusOriginId,
  // which lives as plain useState inside SceneCanvas.tsx for the sibling
  // "Hide Other Branches" feature). Lives HERE rather than as component
  // state because its trigger (a checkbox in ViewPopover.tsx) and its
  // consumer (SceneCanvas.tsx's toFlowNodes) are two separate components -
  // the same reason selectedNodeId/replyTargetNodeId already live on this
  // store rather than in whichever single component happens to set them.
  private focusAcceptedPaths = false;

  constructor(private readonly transport: WsTransport) {}

  private bind<T>(topic: keyof typeof TOPIC_VALIDATORS, assign: (value: T) => void): () => void {
    return this.transport.subscribe(topic, (payload) => {
      const validated = TOPIC_VALIDATORS[topic](payload);
      if (validated.ok) {
        assign(validated.value as T);
        this.emit();
      } else {
        console.error(`[${topic}] rejected snapshot:`, validated.errors);
      }
    });
  }

  connect(): void {
    this.unsubscribers.push(
      this.bind<SceneState>("scene", (v) => (this.scene = v)),
      this.bind<GridControlState>("grid-control", (v) => (this.grid = v)),
      this.bind<DragSpeedState>("drag-speed", (v) => (this.dragConfig = v)),
      this.bind<FontControlState>("font-control", (v) => (this.fontConfig = v)),
    );
  }

  dispose(): void {
    for (const unsubscribe of this.unsubscribers) unsubscribe();
    this.unsubscribers.length = 0;
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getScene = (): SceneState => this.scene;
  getGrid = (): GridControlState => this.grid;
  getDragConfig = (): DragSpeedState => this.dragConfig;
  getFontConfig = (): FontControlState => this.fontConfig;
  getSelectedNodeId = (): string | null => this.selectedNodeId;
  getReplyTargetNodeId = (): string | null => this.replyTargetNodeId;
  getSynthesizeTargetNodeIds = (): string[] | null => this.synthesizeTargetNodeIds;
  getFocusAcceptedPaths = (): boolean => this.focusAcceptedPaths;

  // R5.1: no-op-if-unchanged, same discipline as every other setter here that
  // guards a redundant assignment before paying for an emit() fan-out.
  setSelectedNodeId(id: string | null): void {
    if (id === this.selectedNodeId) return;
    this.selectedNodeId = id;
    this.emit();
  }

  setReplyTargetNodeId(id: string | null): void {
    if (id === this.replyTargetNodeId && this.synthesizeTargetNodeIds === null) return;
    this.replyTargetNodeId = id;
    this.synthesizeTargetNodeIds = null;
    this.emit();
  }

  setSynthesizeTargetNodeIds(ids: string[] | null): void {
    this.synthesizeTargetNodeIds = ids;
    this.replyTargetNodeId = null;
    this.emit();
  }

  setFocusAcceptedPaths(value: boolean): void {
    if (value === this.focusAcceptedPaths) return;
    this.focusAcceptedPaths = value;
    this.emit();
  }

  private emit(): void {
    for (const listener of [...this.listeners]) listener();
  }

  // -- intents (backend/canvas.py's registered surface, 1:1) ---------------

  addNode(x: number, y: number, title = ""): void {
    this.transport.intent("scene", "addNode", [x, y, title]);
  }

  // R3.1: real chat nodes - createChatNode/deleteChatNode/setChatCollapsed
  // mirror backend/canvas.py's intent names 1:1 (same convention as every
  // other scene intent above).
  addChatNode(x: number, y: number, content: string, isUser: boolean, parentId?: string): void {
    const args: unknown[] = [x, y, content, isUser];
    if (parentId !== undefined) args.push(parentId);
    this.transport.intent("scene", "addChatNode", args);
  }

  deleteChatNode(id: string): void {
    this.transport.intent("scene", "deleteChatNode", [id]);
  }

  // R3.5: real code nodes - deletion has no dedicated intent (code nodes are
  // never branch points/reparented, so the generic removeNodes intent below
  // already covers it).
  addCodeNode(x: number, y: number, code: string, language: string, parentId?: string): void {
    const args: unknown[] = [x, y, code, language];
    if (parentId !== undefined) args.push(parentId);
    this.transport.intent("scene", "addCodeNode", args);
  }

  setChatCollapsed(id: string, collapsed: boolean): void {
    this.transport.intent("scene", "setChatCollapsed", [id, collapsed]);
  }

  // R7.5e: legacy's "Collapse All Nodes"/"Expand All Nodes" (graphlink_
  // window_navigation.py:12-13) - a bulk is_collapsed change restricted
  // server-side to chat/conversation/html-kind nodes only. Same plain
  // fire-and-forget shape as setChatCollapsed above and every other no-arg
  // scene intent (organizeNodes, ...): the new is_collapsed values arrive
  // through the next scene snapshot, nothing synchronous needed here.
  collapseAllNodes(): void {
    this.transport.intent("scene", "collapseAllNodes", []);
  }

  expandAllNodes(): void {
    this.transport.intent("scene", "expandAllNodes", []);
  }

  // R3.9/R3.10: real document nodes (attachments). Unlike chat/code,
  // parentId is REQUIRED - the backend's add_document_node signature has no
  // default for it (a document node can never exist without a parent chat
  // node in the legacy app). The five backend keyword-only fields
  // (file_path/mime_type/duration_seconds/byte_size/preview_label) are
  // bundled into one optional `options` object rather than five trailing
  // optional parameters: unlike addChatNode/addCodeNode's single optional
  // trailing parentId (conditionally omitted from the wire args so the
  // backend's own default kicks in), omitting only SOME of five trailing
  // positional slots while supplying a later one is not well-formed over
  // dispatch_intent's plain `handler(*args)` positional call - so this
  // always sends the full 11-arg positional list, filling any field the
  // caller didn't supply with the exact same default the backend method
  // itself uses. Same intent name reused for collapse - see setChatCollapsed
  // below this method's call sites in SceneCanvas.tsx for why.
  addDocumentNode(
    x: number,
    y: number,
    title: string,
    content: string,
    attachmentKind: string,
    parentId: string,
    options: {
      filePath?: string;
      mimeType?: string;
      durationSeconds?: number | null;
      byteSize?: number | null;
      previewLabel?: string;
    } = {},
  ): void {
    const {
      filePath = "",
      mimeType = "",
      durationSeconds = null,
      byteSize = null,
      previewLabel = "",
    } = options;
    this.transport.intent("scene", "addDocumentNode", [
      x,
      y,
      title,
      content,
      attachmentKind,
      parentId,
      filePath,
      mimeType,
      durationSeconds,
      byteSize,
      previewLabel,
    ]);
  }

  // R3.13/R3.14: real thinking nodes + generic docking. addThinkingNode has
  // no real UI creation trigger yet - same situation addCodeNode/
  // addDocumentNode were in when they landed; real creation is R4's agent
  // layer. setNodeDocked is intentionally generic (any node kind, either
  // direction) rather than a thinking-node-specific intent - it backs both
  // ThinkingNodeView's "Dock to Parent Node" and ChatNodeView's per-child
  // "Reveal Docked Items" undock action.
  addThinkingNode(x: number, y: number, thinkingText: string, parentId: string): void {
    this.transport.intent("scene", "addThinkingNode", [x, y, thinkingText, parentId]);
  }

  // R3.17/R3.18: real HTML view nodes. Same posture as addThinkingNode/
  // addDocumentNode - parentId is REQUIRED (the backend's add_html_node has
  // no default for it), and there is no real UI creation trigger yet (R4's
  // agent/plugin layer). The html source string rides the same `content`
  // field every other node kind's text lives in - no new wire field.
  addHtmlNode(x: number, y: number, htmlContent: string, parentId: string): void {
    this.transport.intent("scene", "addHtmlNode", [x, y, htmlContent, parentId]);
  }

  // R3.21/R3.22: real image nodes. Same posture as addThinkingNode/
  // addHtmlNode - no real UI creation trigger yet (R4's agent/plugin layer);
  // this method exists so the intent shape is testable now. The image bytes
  // themselves are never sent over this (or any) WS topic - only the small
  // imageAssetId reference string SceneNodeRow carries rides the wire; the
  // caller is responsible for having already uploaded/generated the bytes
  // and obtained imageBytesBase64 some other way (out of scope here).
  // mimeType defaults to "image/png" to match the backend's own default.
  addImageNode(
    x: number,
    y: number,
    imageBytesBase64: string,
    prompt: string,
    parentId: string,
    mimeType = "image/png",
  ): void {
    this.transport.intent("scene", "addImageNode", [x, y, imageBytesBase64, prompt, parentId, mimeType]);
  }

  // R3.25/R3.26: real conversation nodes - the only R3 kind shaped like a
  // growing message LIST (data.history) rather than one scalar content
  // field. addConversationNode has no real UI creation trigger yet, same
  // posture as addThinkingNode/addHtmlNode/addImageNode before it. Deletion
  // has no dedicated intent (a conversation node is never a branch
  // point/reparented, so the generic removeNodes intent below already
  // covers it, same as code/thinking/html/image); collapse reuses the
  // existing generic setChatCollapsed intent below (see its own comment)
  // rather than inventing a setConversationCollapsed the backend doesn't
  // register.
  addConversationNode(x: number, y: number, parentId: string): void {
    this.transport.intent("scene", "addConversationNode", [x, y, parentId]);
  }

  // sendConversationMessage appends a real user message AND triggers the
  // existing app-wide deferred notification ("AI response generation lands
  // in R4.") over the same notification WS topic the Composer/ChatNode's
  // own sendMessage already flows through - nothing new to wire on the
  // frontend for that half of it.
  sendConversationMessage(id: string, text: string): void {
    this.transport.intent("scene", "sendConversationMessage", [id, text]);
  }

  // No live caller yet this increment (same posture as addThinkingNode when
  // it first landed) - exists so the intent shape is testable now. Will
  // back the real agent reply once R4's agent layer can call it.
  appendConversationAssistantMessage(id: string, text: string): void {
    this.transport.intent("scene", "appendConversationAssistantMessage", [id, text]);
  }

  deleteConversationMessage(id: string, messageIndex: number): void {
    this.transport.intent("scene", "deleteConversationMessage", [id, messageIndex]);
  }

  // R4.3: real per-node Cancel for a conversation node's own in-flight reply.
  // A second, independent registration of the same "cancelChatRequest" intent
  // name the Composer already sends on the "app-composer" topic (see
  // composerStore.ts's own cancelChatRequest) - not the same call site, just
  // two topics sharing one action name, so this is named distinctly
  // (cancelConversationRequest) to avoid implying otherwise.
  cancelConversationRequest(requestId: string): void {
    this.transport.intent("scene", "cancelChatRequest", [requestId]);
  }

  setNodeDocked(id: string, docked: boolean): void {
    this.transport.intent("scene", "setNodeDocked", [id, docked]);
  }

  // R4.3c: real Regenerate Response, for both ChatNodeView's own menu and
  // CodeNodeView's menu (which resolves to its parent chat node's id before
  // calling this - see toFlowNodes below; the backend never kind-sniffs).
  regenerateResponse(chatNodeId: string): void {
    this.transport.intent("scene", "regenerateResponse", [chatNodeId]);
  }

  // R4.4a: real "Generate Image from Text", for ChatNodeView's own menu -
  // resolves purely from the ChatNode's id (backend reads its own .content
  // as the prompt, mirroring legacy's node.text).
  generateImage(chatNodeId: string): void {
    this.transport.intent("scene", "generateImage", [chatNodeId]);
  }

  // R4.4a: real "Regenerate Image", for ImageNodeView's own menu - resolves
  // purely from the ImageNode's id (backend reads ITS OWN .content as the
  // prompt and its parent chat node as the new sibling's parent - see
  // backend/canvas.py's resolve_regenerate_image docstring for why this is a
  // deliberate improvement over legacy's parent-.text reuse).
  regenerateImage(imageNodeId: string): void {
    this.transport.intent("scene", "regenerateImage", [imageNodeId]);
  }

  // R5.1: real Web Research plugin - runWebResearch starts (or restarts) a
  // research run for an existing web_research node; cancelWebResearchRequest
  // targets it by its own in-flight requestId, the same
  // requestId-not-nodeId shape cancelConversationRequest above already
  // established for the conversation node's own per-node cancel.
  runWebResearch(nodeId: string, query: string): void {
    this.transport.intent("scene", "runWebResearch", [nodeId, query]);
  }

  cancelWebResearchRequest(requestId: string): void {
    this.transport.intent("scene", "cancelWebResearchRequest", [requestId]);
  }

  // R5.2: real Artifact/Drafter plugin - sendArtifactMessage appends a real
  // user instruction AND triggers ArtifactAgent.get_response(current_artifact,
  // history) for an existing artifact node; cancelArtifactRequest targets it
  // by its own in-flight requestId, the same requestId-not-nodeId shape
  // cancelConversationRequest/cancelWebResearchRequest above already
  // established for their own per-node cancel.
  sendArtifactMessage(nodeId: string, text: string): void {
    this.transport.intent("scene", "sendArtifactMessage", [nodeId, text]);
  }

  cancelArtifactRequest(requestId: string): void {
    this.transport.intent("scene", "cancelArtifactRequest", [requestId]);
  }

  // R5.3: real Gitlink plugin - nine intents backing the three-tab
  // Setup/Context/Proposal flow (see GitlinkNodeView.tsx's own module doc for
  // the full per-tab breakdown). fetchGitlinkRepositories/fetchGitlinkContext
  // are the only two of the nine that need a REPLY (a repo name list / the
  // lazily-fetched context XML body) rather than a fire-and-forget push
  // through the next scene snapshot - transport.intent() is declared
  // `: void` (confirmed by reading transport.ts: it is fire-and-forget,
  // dropped silently pre-connect, no return value at all), so it cannot be
  // what "returns a Promise" means here. transport.request() is the real
  // request/response primitive for that (the same one transport.test.ts's
  // own "system"/"ping" round-trip exercises) - these two ride that instead.
  // Every other Gitlink method below stays on the ordinary intent() path,
  // exactly like every other scene intent above.
  fetchGitlinkRepositories(nodeId: string): Promise<string[]> {
    return this.transport.request("scene", "fetchGitlinkRepositories", [nodeId]) as Promise<string[]>;
  }

  loadGitlinkRepoTree(nodeId: string, repo: string, branch: string): void {
    this.transport.intent("scene", "loadGitlinkRepoTree", [nodeId, repo, branch]);
  }

  setGitlinkLocalRoot(nodeId: string, localRoot: string): void {
    this.transport.intent("scene", "setGitlinkLocalRoot", [nodeId, localRoot]);
  }

  // Opens the real native OS folder picker (backend/native_dialogs.py, the
  // same primitive Settings' Ollama/Llama.cpp Scan Folder buttons already
  // use) and, on a folder being picked, sets it server-side exactly like
  // setGitlinkLocalRoot above - fire-and-forget, the new value arrives back
  // through the next scene snapshot rather than a direct reply.
  pickGitlinkLocalRoot(nodeId: string): void {
    this.transport.intent("scene", "pickGitlinkLocalRoot", [nodeId]);
  }

  importGitlinkSnapshot(nodeId: string, repo: string, branch: string): void {
    this.transport.intent("scene", "importGitlinkSnapshot", [nodeId, repo, branch]);
  }

  // scopeMode/selectedPaths are never independently mirrored server-side via
  // their own setter intent (see GitlinkNodeView.tsx's Setup tab doc) - they
  // only ever travel as parameters of this one call, read from local
  // component state at the moment Build Context is clicked.
  buildGitlinkContext(nodeId: string, scopeMode: string, selectedPaths: string[]): void {
    this.transport.intent("scene", "buildGitlinkContext", [nodeId, scopeMode, selectedPaths]);
  }

  fetchGitlinkContext(nodeId: string): Promise<string> {
    return this.transport.request("scene", "fetchGitlinkContext", [nodeId]) as Promise<string>;
  }

  runGitlinkChangeSet(nodeId: string, taskPrompt: string): void {
    this.transport.intent("scene", "runGitlinkChangeSet", [nodeId, taskPrompt]);
  }

  // Same requestId-not-nodeId shape cancelConversationRequest/
  // cancelWebResearchRequest/cancelArtifactRequest above already established
  // for their own per-node cancel.
  cancelGitlinkRequest(requestId: string): void {
    this.transport.intent("scene", "cancelGitlinkRequest", [requestId]);
  }

  // fingerprint is passed through verbatim - the caller (GitlinkNodeView's
  // Apply confirmation) must pass exactly the server's own last-sent
  // gitlinkChangeFingerprint, never anything computed client-side; this
  // store method has no opinion on that, it just forwards the argument.
  applyGitlinkChanges(nodeId: string, fingerprint: string): void {
    this.transport.intent("scene", "applyGitlinkChanges", [nodeId, fingerprint]);
  }

  // R5.4: Py-Coder node - setPyCoderMode/runPyCoder/cancelPyCoderRequest
  // mirror backend/canvas.py's registered intent names 1:1, same convention
  // as every scene intent above. mode is a plain string ("ai_driven" |
  // "manual") - the backend (SceneDocument.set_pycoder_mode) is the one and
  // only validator of that value; this store has no opinion on it, same
  // posture as applyGitlinkChanges's fingerprint passthrough above.
  setPyCoderMode(nodeId: string, mode: string): void {
    this.transport.intent("scene", "setPyCoderMode", [nodeId, mode]);
  }

  // inputText's meaning (a natural-language prompt vs hand-typed code) is
  // entirely a function of the node's CURRENT server-side pycoder_mode -
  // this store (and the WS intent itself) is mode-agnostic, matching
  // backend/canvas.py's own start_pycoder_run docstring ("stores input_text
  // into the field the CURRENT mode actually reads at dispatch time").
  runPyCoder(nodeId: string, inputText: string): void {
    this.transport.intent("scene", "runPyCoder", [nodeId, inputText]);
  }

  // Same requestId-not-nodeId shape cancelConversationRequest/
  // cancelWebResearchRequest/cancelArtifactRequest/cancelGitlinkRequest above
  // already established for their own per-node cancel.
  cancelPyCoderRequest(requestId: string): void {
    this.transport.intent("scene", "cancelPyCoderRequest", [requestId]);
  }

  // R5.4: Execution Sandbox node - same three-intent shape as Py-Coder above
  // (setCodeSandboxRequirements/runCodeSandbox/cancelCodeSandboxRequest),
  // minus a mode toggle - backend/canvas.py's start_code_sandbox_run has no
  // mode-dependent field split (see its own docstring); a run's input_text
  // always lands in code_sandbox_prompt, and an empty prompt is a legitimate
  // "re-run the existing code_sandbox_code" request, not an error, at the
  // WS-intent layer - CodeSandboxNodeView is the one that decides whether
  // that's currently sensible to allow (see its own Run-enablement comment).
  setCodeSandboxRequirements(nodeId: string, requirementsText: string): void {
    this.transport.intent("scene", "setCodeSandboxRequirements", [nodeId, requirementsText]);
  }

  // ADR-005 stage 5.5: the approval panel's own source-build opt-in
  // checkbox - fires immediately on toggle, same posture as
  // setCodeSandboxRequirements above, not deferred to Approve.
  setCodeSandboxAllowSourceBuilds(nodeId: string, allow: boolean): void {
    this.transport.intent("scene", "setCodeSandboxAllowSourceBuilds", [nodeId, allow]);
  }

  runCodeSandbox(nodeId: string, inputText: string): void {
    this.transport.intent("scene", "runCodeSandbox", [nodeId, inputText]);
  }

  cancelCodeSandboxRequest(requestId: string): void {
    this.transport.intent("scene", "cancelCodeSandboxRequest", [requestId]);
  }

  // R5.4: the shared human-approval gate - ONE request_id namespace across
  // both Py-Coder and Execution Sandbox (backend/agents.py's
  // AgentDispatcher._resolve_approval looks the id up across both request
  // dicts), so these two intents are not duplicated per-kind. Both take
  // ONLY a requestId - never a node id, never the code itself - mirroring
  // applyGitlinkChanges's own "this store method has no opinion on the
  // content, it just forwards the caller's requestId" posture; the caller
  // (CodeExecutionApprovalPanel via SceneCanvas's toFlowNodes closures) is
  // responsible for that requestId always being the CURRENT
  // pendingRequestId the scene snapshot says is in flight for that node,
  // never anything UI-supplied.
  approveCodeExecution(requestId: string): void {
    this.transport.intent("scene", "approveCodeExecution", [requestId]);
  }

  denyCodeExecution(requestId: string): void {
    this.transport.intent("scene", "denyCodeExecution", [requestId]);
  }

  // R5.4: NOT one of the 8 registered WS intents above - a thin passthrough
  // to the transport's own subscribeStream() (already exercised by R4.4's
  // token streaming through composerStore.ts's own syncStream), exposed here
  // so CodeSandboxNodeView's live terminal pane can subscribe to a run's
  // stream frames (keyed by its own pendingRequestId) without needing direct
  // access to the private `transport` field. No backend registration is
  // needed for this - subscribeStream is a pure client-side fan-out over
  // `kind:"stream"` frames the server already broadcasts unconditionally
  // (see transport.ts's own doc comment on it).
  subscribeStream(requestId: string, listener: StreamListener): () => void {
    return this.transport.subscribeStream(requestId, listener);
  }

  // R3.3: the Composer's real Send action - a real user ChatNode. The
  // assistant's reply is deferred to R4 (graphlink_config.py's Qt/non-Qt
  // split is a prerequisite for calling the real agent layer); the backend
  // surfaces that honestly via the existing notification topic, no fake
  // response synthesized here.
  //
  // ADR-002 Workstream 1: consumes replyTargetNodeId (if a "Branch from
  // here" pick is pending) as this one send's branch_from_node_id override,
  // then clears it - so it applies to exactly one send, never lingers onto
  // a later, unrelated one. Callers never pass a branch target directly;
  // they call setReplyTargetNodeId first (see that setter's own comment).
  //
  // ADR-002 Workstream 1 ("Synthesize Branches"): checked FIRST, ahead of
  // replyTargetNodeId - if a synthesis selection is staged, this send's
  // text is the user's synthesis instructions, not an ordinary message, so
  // it fires the dedicated synthesizeBranches intent instead of sendMessage
  // and returns early. The two staging fields are already kept mutually
  // exclusive by their own setters, so this branch and the replyTargetNodeId
  // branch below it can never both apply to the same call.
  sendMessage(text: string): void {
    const synthesizeNodeIds = this.synthesizeTargetNodeIds;
    if (synthesizeNodeIds !== null) {
      this.transport.intent("scene", "synthesizeBranches", [synthesizeNodeIds, text]);
      this.setSynthesizeTargetNodeIds(null);
      return;
    }
    const branchFromNodeId = this.replyTargetNodeId;
    const args: unknown[] = [text];
    if (branchFromNodeId !== null) args.push(branchFromNodeId);
    this.transport.intent("scene", "sendMessage", args);
    if (branchFromNodeId !== null) this.setReplyTargetNodeId(null);
  }

  moveNode(id: string, x: number, y: number): void {
    this.transport.intent("scene", "moveNode", [id, x, y]);
  }

  // R6.1 follow-up: a group drag's commit (the group's own node PLUS every
  // transitive member) uses this instead of N individual moveNode calls -
  // see backend/canvas.py's SceneDocument.move_nodes for why calling
  // moveNode once per node published a scene snapshot after EACH one
  // landed, rendering as a visible stretch-then-resettle glitch on every
  // group drag release once the group-bounds recompute could genuinely
  // grow a box (rather than staying frozen, the bug the growth logic
  // itself was fixing).
  moveNodes(positions: Array<{ id: string; x: number; y: number }>): void {
    this.transport.intent(
      "scene",
      "moveNodes",
      [positions.map((p) => [p.id, p.x, p.y])],
    );
  }

  removeNodes(ids: string[]): void {
    if (ids.length > 0) this.transport.intent("scene", "removeNodes", [ids]);
  }

  connectNodes(source: string, target: string): void {
    this.transport.intent("scene", "connectNodes", [source, target]);
  }

  removeEdges(ids: string[]): void {
    if (ids.length > 0) this.transport.intent("scene", "removeEdges", [ids]);
  }

  addPin(title: string, x: number, y: number, note = ""): void {
    this.transport.intent("scene", "addPin", [title, x, y, note]);
  }

  updatePin(id: string, title: string, note: string): void {
    this.transport.intent("scene", "updatePin", [id, title, note]);
  }

  removePin(id: string): void {
    this.transport.intent("scene", "removePin", [id]);
  }

  setSnapToGrid(enabled: boolean): void {
    this.transport.intent("scene", "setSnapToGrid", [enabled]);
  }

  // R7.5b-1: same bare-bool/"scene"-topic shape as setSnapToGrid above.
  setFadeConnections(enabled: boolean): void {
    this.transport.intent("scene", "setFadeConnections", [enabled]);
  }

  // R7.5b-2: same shape again - intent name matches the legacy
  // GridControlBridge's own setOrthogonalConnections Slot name 1:1.
  setOrthogonalConnections(enabled: boolean): void {
    this.transport.intent("scene", "setOrthogonalConnections", [enabled]);
  }

  // R7.5b-3: the fourth and final legacy grid-control toggle.
  setSmartGuides(enabled: boolean): void {
    this.transport.intent("scene", "setSmartGuides", [enabled]);
  }

  setDragFactor(factor: number): void {
    this.transport.intent("scene", "setDragFactor", [factor]);
  }

  organizeNodes(): void {
    this.transport.intent("scene", "organizeNodes", []);
  }

  // R6.5: session save - targets "app-chat-library", not "scene", since
  // Save is a chat-library concern (it needs chat_id/title bookkeeping the
  // scene topic has no notion of), matching loadChat's own home despite
  // being triggered from a different UI surface (the app bar's Save
  // button, not the library dialog) - an intent's topic is about which
  // backend module owns it, not which component happens to dispatch it
  // (see this file's own "grid-control" calls just below for the same
  // precedent: not every method here targets "scene").
  saveChat(): void {
    this.transport.intent("app-chat-library", "saveChat", []);
  }

  // R7.5a: command-palette's "New Chat" - same "app-chat-library", not
  // "scene", topic-ownership reasoning as saveChat above; newChat already
  // exists and is wired from the chat-library dialog, this just gives the
  // command palette a second entry point to the same real intent.
  newChat(): void {
    this.transport.intent("app-chat-library", "newChat", []);
  }

  // -- R6.1: Notes/Frames/Containers ----------------------------------------
  //
  // Mirrors backend/canvas.py's register_canvas() intent names/argument
  // order 1:1, same convention as every scene intent above. addNote/
  // createFrame/createContainer all return the new node's id server-side
  // (register_canvas's own handlers `return node.id`), but - same posture as
  // every other addXNode method above (addChatNode, addThinkingNode,
  // addHtmlNode, addImageNode, addConversationNode, ...) - this stays a
  // plain fire-and-forget intent() call, not request(): the new node arrives
  // through the next scene snapshot like any other mutation, and nothing on
  // the frontend needs the id synchronously the way fetchGitlinkRepositories/
  // fetchGitlinkContext genuinely do.

  addNote(x: number, y: number, options: { isSystemPrompt?: boolean; isSummaryNote?: boolean } = {}): void {
    const { isSystemPrompt = false, isSummaryNote = false } = options;
    this.transport.intent("scene", "addNote", [x, y, isSystemPrompt, isSummaryNote]);
  }

  setNoteContent(nodeId: string, content: string): void {
    this.transport.intent("scene", "setNoteContent", [nodeId, content]);
  }

  createFrame(itemIds: string[]): void {
    this.transport.intent("scene", "createFrame", [itemIds]);
  }

  createContainer(itemIds: string[]): void {
    this.transport.intent("scene", "createContainer", [itemIds]);
  }

  // ADR-002 Workstream 1 ("Compare Branches") - same fire-and-forget shape
  // as createFrame/createContainer above: the frontend already gathered
  // React Flow's own multi-selection (App.tsx's GlobalShortcuts), the
  // backend validates/does the work, and the resulting note arrives
  // through the next scene snapshot like any other mutation.
  compareBranches(nodeIds: string[]): void {
    this.transport.intent("scene", "compareBranches", [nodeIds]);
  }

  // ADR-002 Workstream 1 ("Branch status and lifecycle") - three plain
  // fire-and-forget setters, same posture as setGroupLabel/setGroupColor
  // below: the backend validates/does the work, and the new value arrives
  // through the next scene snapshot like any other mutation.
  setBranchStatus(nodeId: string, status: string): void {
    this.transport.intent("scene", "setBranchStatus", [nodeId, status]);
  }

  setFinalDeliverable(nodeId: string, isFinal: boolean): void {
    this.transport.intent("scene", "setFinalDeliverable", [nodeId, isFinal]);
  }

  collapseBranch(nodeId: string, collapsed: boolean): void {
    this.transport.intent("scene", "collapseBranch", [nodeId, collapsed]);
  }

  // Shared setter for frame/container header-note/title text (backend/
  // canvas.py's set_group_label) - reused verbatim for both kinds, same
  // posture as setGroupColor below.
  setGroupLabel(nodeId: string, text: string): void {
    this.transport.intent("scene", "setGroupLabel", [nodeId, text]);
  }

  // Shared color setter for note/frame/container kinds. Either argument may
  // be null to clear that half back to "derive from default" - the caller
  // (GroupColorPicker's "Reset to Default" item) passes (null, null) for a
  // full reset, or one real hex + null for a single-half set.
  setGroupColor(nodeId: string, color: string | null, headerColor: string | null): void {
    this.transport.intent("scene", "setGroupColor", [nodeId, color, headerColor]);
  }

  toggleFrameLock(nodeId: string): void {
    this.transport.intent("scene", "toggleFrameLock", [nodeId]);
  }

  toggleGroupCollapsed(nodeId: string): void {
    this.transport.intent("scene", "toggleGroupCollapsed", [nodeId]);
  }

  resizeFrame(nodeId: string, width: number, height: number): void {
    this.transport.intent("scene", "resizeFrame", [nodeId, width, height]);
  }

  fitFrameToContent(nodeId: string): void {
    this.transport.intent("scene", "fitFrameToContent", [nodeId]);
  }

  ungroup(nodeId: string): void {
    this.transport.intent("scene", "ungroup", [nodeId]);
  }

  // -- R6.2: Chart node -----------------------------------------------------
  //
  // Mirrors backend/canvas.py's register_canvas() intent names/argument order
  // 1:1, same convention as every scene intent above. generateChart returns
  // the new chart node's id server-side (or null + a notification on invalid
  // parent/chartType), but - same posture as every other addXNode-style
  // method above (addChatNode, addNote, createFrame, ...) - this stays a
  // plain fire-and-forget intent() call, not request(): the new node arrives
  // through the next scene snapshot, and nothing on the frontend needs the id
  // synchronously (ChatNodeView's Generate Chart submenu just fires-and-
  // forgets, same as its own onGenerateImage).

  generateChart(parentNodeId: string, chartType: string): void {
    this.transport.intent("scene", "generateChart", [parentNodeId, chartType]);
  }

  // R8a: the Key Takeaway / Explainer Note agents, restored from the deleted
  // Qt app (their menu items had been disabled stubs blaming a missing agent
  // layer that had in fact shipped in R4). Both take only the source chat
  // node's id - the backend reads its own .content, exactly like
  // generateImage - and the resulting note arrives on the next scene
  // snapshot, so neither needs the new node id back here.
  generateKeyTakeaway(sourceNodeId: string): void {
    this.transport.intent("scene", "generateKeyTakeaway", [sourceNodeId]);
  }

  generateExplainerNote(sourceNodeId: string): void {
    this.transport.intent("scene", "generateExplainerNote", [sourceNodeId]);
  }

  resizeChart(nodeId: string, width: number, height: number): void {
    this.transport.intent("scene", "resizeChart", [nodeId, width, height]);
  }

  toggleChartAspectLock(nodeId: string): void {
    this.transport.intent("scene", "toggleChartAspectLock", [nodeId]);
  }

  // -- R6.3: Scene-level serialization gaps ---------------------------------
  //
  // Mirrors backend/canvas.py's register_canvas() intent names/argument order
  // 1:1, same convention as every scene intent above. These three back the
  // legacy session serializer's own view_state/HTML splitter_state/chat
  // scroll_value fields - not because anything reads them back INTO this
  // increment's UI yet, but because R6.4 (session LOAD) and R6.5 (session
  // SAVE) need somewhere real on the document/node model to persist and
  // restore them; without these, a value present in an old chats.db row (or
  // one a user sets by panning/zooming/dragging/scrolling in this session)
  // would have nowhere to land. Fire-and-forget, same posture as every other
  // plain setter intent above (setChatCollapsed, resizeChart, ...) - no reply
  // needed, the next scene snapshot is enough if the value round-trips
  // through it at all.

  setViewState(zoomFactor: number, scrollX: number, scrollY: number): void {
    this.transport.intent("scene", "setViewState", [zoomFactor, scrollX, scrollY]);
  }

  setHtmlSplitterState(nodeId: string, value: number): void {
    this.transport.intent("scene", "setHtmlSplitterState", [nodeId, value]);
  }

  setChatScrollValue(nodeId: string, value: number): void {
    this.transport.intent("scene", "setChatScrollValue", [nodeId, value]);
  }

  // Grid intents ride the grid-control topic; font intents ride scene - both
  // keep the legacy bridges' @Slot names 1:1 (backend/canvas.py contract).
  setGridSize(size: number): void {
    this.transport.intent("grid-control", "setGridSize", [size]);
  }

  setGridOpacityPercent(percent: number): void {
    this.transport.intent("grid-control", "setGridOpacityPercent", [percent]);
  }

  setGridStyle(style: string): void {
    this.transport.intent("grid-control", "setGridStyle", [style]);
  }

  setGridColor(hex: string): void {
    this.transport.intent("grid-control", "setGridColor", [hex]);
  }

  setFontFamily(family: string): void {
    this.transport.intent("scene", "setFontFamily", [family]);
  }

  setFontSize(sizePt: number): void {
    this.transport.intent("scene", "setFontSize", [sizePt]);
  }

  setFontColor(hex: string): void {
    this.transport.intent("scene", "setFontColor", [hex]);
  }

  // Rides the notification topic, not scene - same "this store already
  // spans more than one topic" precedent as the grid-control intents above.
  // Document View's empty-content guard (SceneCanvas.tsx's toFlowNodes) is
  // the first caller: a genuinely frontend-only condition (the node's own
  // already-synced content is blank) that still needs the app's one shared
  // notification banner, which is server-authoritative state - see
  // backend/notifications.py's showInfo intent for why this exists instead
  // of a second, parallel client-local notification UI.
  showInfoNotification(message: string): void {
    this.transport.intent("notification", "showInfo", [message]);
  }
}

/** start + (proposed - start) * factor: the drag-speed contract carried over
 * from the Qt canvas (ChatView's drag factor scaled item motion the same
 * way). Exported standalone for direct unit testing. */
export function scaleDragPosition(
  start: { x: number; y: number },
  proposed: { x: number; y: number },
  factor: number,
): { x: number; y: number } {
  return {
    x: start.x + (proposed.x - start.x) * factor,
    y: start.y + (proposed.y - start.y) * factor,
  };
}
