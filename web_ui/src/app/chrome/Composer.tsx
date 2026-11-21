import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import type { ChatKnowledgePreference, SceneStore } from "../canvas/sceneStore";
import { KnowledgeFileImportStatus, useKnowledgeFileImport } from "../knowledgeFileImport";
import type { StateListener } from "../../lib/ws/transport";
import { Popover, useOverlays } from "../overlays/overlays";
import type { ComparisonModel, ComposerStore } from "./composerStore";
import { TokenCounter } from "./TokenCounter";
import {
  HarnessExecutionProfileSelect, MAX_HARNESS_TASK_LENGTH, exceedsHarnessTaskLimit, type HarnessExecutionProfile,
} from "./HarnessLaunchOptions";

/**
 * The composer dock (Qt-removal plan R2.3/R3.3/R4.3) - ComposerApp's SPA
 * successor.
 *
 * Real here: draft text editing, reasoning-level selection (a stored
 * preference popover, reusing the overlay system rather than a dedicated
 * picker island), Send - a real user ChatNode via sceneStore.sendMessage,
 * (R4.3) the agent-dispatch request lifecycle: the assistant's reply is
 * generated asynchronously by the backend agent layer and lands over the
 * existing scene topic republish, exactly like any other node-creation
 * path this app already handles, (R8a) real chat-model selection (a
 * popover next to reasoning, writing through the same assignment the
 * Settings > Ollama page uses), and (R8a) real file attachments - a native
 * dialog stages an image/audio/document, backend/attachments.py classifies
 * and (for documents) extracts it, and staged items render as removable
 * chips above the input until Send bundles them onto the new ChatNode.
 * Send is gated on request.canSend (so a second send can't be issued
 * mid-flight) and Cancel is rendered only while request.canCancel is true,
 * per backend/composer.py's request capability flags.
 *
 * Still visibly deferred: provider-MODE switching (Ollama/Llama.cpp/API) -
 * Settings configures all three for real, but changing which one is active
 * needs real provider-switch wiring the app bar's own disabled selector
 * names. Context review (inspecting/editing a staged attachment's content
 * before Send) is also not built - remove-and-reattach is the only
 * correction available today.
 *
 * Theme is NOT read from this payload (see backend/composer.py's docstring
 * for why) - the SPA's tokens are already global CSS.
 */

const LazyModelComparisonPicker = lazy(() =>
  import("./ModelComparisonPicker").then((m) => ({ default: m.ModelComparisonPicker })),
);

function Icon({ name }: { name: "attach" | "send" | "chevron" | "stop" | "compare" | "reasoning" | "agent" | "knowledge" }) {
  const paths: Record<string, string> = {
    attach:
      "M12 5.5 6.4 11.1a3.6 3.6 0 0 0 5.1 5.1l6-6a2.5 2.5 0 0 0-3.5-3.5l-6.1 6.1a1.35 1.35 0 0 0 1.9 1.9l5.5-5.5",
    send: "M12 19V5m-6 6 6-6 6 6",
    chevron: "m7 10 5 5 5-5",
    stop: "M7 7h10v10H7z",
    compare: "M9 8V5.5A1.5 1.5 0 0 1 10.5 4h8A1.5 1.5 0 0 1 20 5.5v8a1.5 1.5 0 0 1-1.5 1.5H16M5.5 9h8A1.5 1.5 0 0 1 15 10.5v8a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 18.5v-8A1.5 1.5 0 0 1 5.5 9Z",
    reasoning: "M12 3.5v2m0 13v2M3.5 12h2m13 0h2M6 6l1.4 1.4m9.2 9.2L18 18M6 18l1.4-1.4m9.2-9.2L18 6M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z",
    agent: "M8 4h8m-4 0v3m-7 6V9a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v4m-14 0v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4M9 12h.01M15 12h.01M9 16h6",
    knowledge: "M12 6.5a6 6 0 0 0-8-1v13a6 6 0 0 1 8 1m0-13a6 6 0 0 1 8-1v13a6 6 0 0 0-8 1m0-13v13",
  };
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
      <path d={paths[name]} />
    </svg>
  );
}

export function Composer({
  store,
  sceneStore,
  showTokenCounter = true,
  contextInspector,
}: {
  store: ComposerStore;
  sceneStore: SceneStore;
  showTokenCounter?: boolean;
  /** Rendered in the control rail beside Attach, so it never floats over
   * the canvas HUD (it used to sit on top of the minimap). */
  contextInspector?: ReactNode | ((useKnowledge: boolean | null, targetNodeId: string | null | undefined) => ReactNode);
}) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const streamText = useSyncExternalStore(store.subscribe, store.getStreamText);
  const overlays = useOverlays();
  const subscribeKnowledge = useCallback(
    (listener: StateListener) => store.subscribeKnowledgeIngest(listener), [store],
  );
  const knowledgeImport = useKnowledgeFileImport(subscribeKnowledge);

  // ADR-002 Workstream 1 ("Branch from here"): sceneStore's own
  // replyTargetNodeId/scene, subscribed here (not just in SceneCanvas) so
  // this indicator re-renders both when a node is picked and when the
  // target's own content changes or the node itself is deleted. A stale
  // target (deleted mid-pick) is treated as no target - same "derive an
  // effective value, don't force-clear the store" posture SceneCanvas.tsx's
  // own isBranchFocusOriginValid already uses for the sibling "Hide Other
  // Branches" feature - so a dangling id can never render a fork indicator
  // for a node that no longer exists.
  const scene = useSyncExternalStore(sceneStore.subscribe, sceneStore.getScene);
  const replyTargetNodeId = useSyncExternalStore(sceneStore.subscribe, sceneStore.getReplyTargetNodeId);
  const replyTargetNode = replyTargetNodeId ? scene.nodes.find((n) => n.id === replyTargetNodeId) : undefined;
  const selectedNodeId = useSyncExternalStore(sceneStore.subscribe, sceneStore.getSelectedNodeId);
  const selectedAgent = scene.nodes.find((n) => n.id === selectedNodeId && n.kind === "harness");

  // A fresh scene's initial snapshot may have the SAME revision (0) as the
  // placeholder. Track the snapshot itself so a lookup rejected before the
  // socket opens is retried on load, as well as on later scene updates.
  // Library imports/removals do not change the scene, so they have their own
  // small versioned topic. Both signals invalidate in-flight lookups.
  const [knowledgeLibraryRevision, setKnowledgeLibraryRevision] = useState<number | null>(null);
  useEffect(() => sceneStore.subscribeKnowledgeLibrary((payload) => {
    if (typeof payload.revision === "number") setKnowledgeLibraryRevision(payload.revision);
  }), [sceneStore]);

  const knowledgeTargetNodeId = replyTargetNode?.id ?? null;
  const [knowledgeResult, setKnowledgeResult] = useState<{
    store: SceneStore;
    targetNodeId: string | null;
    sceneSnapshot: object;
    libraryRevision: number | null;
    preference: ChatKnowledgePreference | null;
  } | null>(null);
  const knowledgeResolved = knowledgeResult?.store === sceneStore &&
    knowledgeResult.targetNodeId === knowledgeTargetNodeId &&
    knowledgeResult.sceneSnapshot === scene &&
    knowledgeResult.libraryRevision === knowledgeLibraryRevision;
  const knowledgePreference = knowledgeResolved ? knowledgeResult!.preference : null;
  const knowledgeCheckFailed = knowledgeResolved && knowledgeResult!.preference === null;
  const [knowledgeUpdating, setKnowledgeUpdating] = useState(false);
  useEffect(() => {
    let current = true;
    void sceneStore.getChatKnowledgePreference(knowledgeTargetNodeId).then(
      (preference) => {
        if (current) setKnowledgeResult((previous) => {
          // A root toggle is only persisted with the first Send. A library
          // refresh must not erase that explicit, unsent choice.
          const unsentOverride = preference.nodeId === null &&
            previous?.store === sceneStore &&
            previous.sceneSnapshot === scene &&
            previous.targetNodeId === knowledgeTargetNodeId &&
            previous.preference?.nodeId === null
              ? previous.preference.override
              : null;
          return {
            store: sceneStore, targetNodeId: knowledgeTargetNodeId,
            sceneSnapshot: scene, libraryRevision: knowledgeLibraryRevision,
            preference: unsentOverride === null
              ? preference
              : { ...preference, enabled: unsentOverride, override: unsentOverride },
          };
        });
      },
      () => {
        if (current) setKnowledgeResult({
          store: sceneStore, targetNodeId: knowledgeTargetNodeId,
          sceneSnapshot: scene, libraryRevision: knowledgeLibraryRevision, preference: null,
        });
      },
    );
    return () => { current = false; };
  }, [sceneStore, knowledgeTargetNodeId, scene, knowledgeLibraryRevision]);

  function updateKnowledgePreference(
    next: (current: ChatKnowledgePreference | null) => ChatKnowledgePreference | null,
  ) {
    setKnowledgeResult((result) => result?.store === sceneStore &&
      result.targetNodeId === knowledgeTargetNodeId &&
      result.sceneSnapshot === scene &&
      result.libraryRevision === knowledgeLibraryRevision
        ? { ...result, preference: next(result.preference) }
        : result);
  }

  function toggleUseKnowledge() {
    if (!knowledgePreference || knowledgeUpdating) return;
    const preference = knowledgePreference;
    const enabled = !preference.enabled;
    updateKnowledgePreference(() => ({ ...preference, enabled, override: enabled }));
    if (preference.nodeId === null) return; // Sent with the first new root message.
    setKnowledgeUpdating(true);
    void (async () => {
      try {
        await sceneStore.setChatUseKnowledge(preference.nodeId!, enabled);
      } catch {
        try {
          const fresh = await sceneStore.getChatKnowledgePreference(preference.nodeId);
          updateKnowledgePreference((now) => now?.nodeId === preference.nodeId ? fresh : now);
        } catch {
          updateKnowledgePreference((now) => now?.nodeId === preference.nodeId ? null : now);
        }
      } finally {
        setKnowledgeUpdating(false);
      }
    })();
  }

  // ADR-002 Workstream 1 ("Synthesize Branches"): the list-valued sibling of
  // replyTargetNodeId above - same subscription reasoning (re-renders if a
  // staged node is deleted mid-pick). sceneStore keeps the two mutually
  // exclusive (see setSynthesizeTargetNodeIds's own comment), so in practice
  // at most one of replyTargetNode/synthesizeTargetNodeIds is ever non-empty
  // - each indicator below still guards on its own value independently
  // rather than on an explicit else, so that invariant is never load-bearing
  // for correctness here, only for which one the user happens to see.
  const synthesizeTargetNodeIds = useSyncExternalStore(
    sceneStore.subscribe,
    sceneStore.getSynthesizeTargetNodeIds,
  );

  // Comparison is opt-in; the ordinary single-model Send never changes its
  // intent or model picker. Selection is an exact provider/model pair.
  const [agentMode, setAgentMode] = useState(false);
  const [agentSending, setAgentSending] = useState(false);
  const [agentError, setAgentError] = useState("");
  const [newAgentProfile, setNewAgentProfile] = useState<HarnessExecutionProfile>("host");
  const [comparisonMode, setComparisonMode] = useState(false);
  const [comparisonOptions, setComparisonOptions] = useState<ComparisonModel[]>([]);
  const [selectedComparisonKeys, setSelectedComparisonKeys] = useState<string[]>([]);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState("");
  const comparisonKey = (model: ComparisonModel) => JSON.stringify([model.provider, model.modelId]);
  const selectedComparisonModels = comparisonOptions.filter(
    (model) => selectedComparisonKeys.includes(comparisonKey(model)),
  );
  const comparisonReady = !comparisonMode || selectedComparisonModels.length >= 2;

  async function openComparisonPicker() {
    setComparisonMode(true);
    overlays.toggle("model-comparison", "popover");
    if (overlays.isOpen("model-comparison")) return;
    setComparisonLoading(true);
    setComparisonError("");
    try {
      const models = await store.loadComparisonModels();
      setComparisonOptions(models);
      setSelectedComparisonKeys((current) =>
        current.filter((key) => models.some((model) => comparisonKey(model) === key)),
      );
    } catch (error) {
      setComparisonError(error instanceof Error ? error.message : "Unable to list comparison models.");
    } finally {
      setComparisonLoading(false);
    }
  }

  const modelLabel = composer.route.modelLabel || composer.route.modelId || "Select a model";
  const isSynthesizing = !!synthesizeTargetNodeIds && synthesizeTargetNodeIds.length > 0;

  function send() {
    const text = composer.draft.text.trim();
    if (!text || !composer.request.canSend || knowledgeUpdating) return;
    if (agentMode) {
      if (exceedsHarnessTaskLimit(text)) {
        setAgentError(`Agent tasks cannot exceed ${MAX_HARNESS_TASK_LENGTH.toLocaleString()} characters. Shorten the task; your draft is preserved.`);
        return;
      }
      if (agentSending || !composer.route.available || !composer.route.modelId) return;
      setAgentSending(true);
      setAgentError("");
      void sceneStore.sendComposerAgentMessage(text, newAgentProfile)
        .then((started) => {
          if (!started) {
            setAgentError("The workspace agent could not start. See the notification for details.");
          } else if (store.getComposer().draft.text.trim() === text) {
            // A rejected/failed dispatch or a draft edited while waiting must
            // never be lost. Unlike chat sends, this has an explicit reply.
            store.updateDraft("");
          }
        })
        .catch(() => setAgentError("Could not send to the workspace agent. Try again."))
        .finally(() => setAgentSending(false));
      return;
    }
    if (comparisonMode && !isSynthesizing) {
      if (!comparisonReady) return;
      const models = selectedComparisonModels.map(({ provider, modelId }) => ({ provider, modelId }));
      const onSettled = (succeeded: boolean) => {
        if (!succeeded) return; // preserve the draft and staged branch target
        if (replyTargetNode?.id) sceneStore.setReplyTargetNodeId(null);
        if (store.getComposer().draft.text === text) store.updateDraft("");
      };
      const firstRootOverride = knowledgePreference?.nodeId === null ? knowledgePreference.override : null;
      if (firstRootOverride !== null && firstRootOverride !== undefined) {
        store.sendModelComparison(text, models, replyTargetNode?.id ?? null, onSettled, firstRootOverride);
      } else {
        store.sendModelComparison(text, models, replyTargetNode?.id ?? null, onSettled);
      }
      return;
    }
    // Unmodified single-model and Synthesize Branches paths.
    const firstRootOverride = knowledgePreference?.nodeId === null ? knowledgePreference.override : null;
    if (firstRootOverride !== null && firstRootOverride !== undefined) {
      sceneStore.sendMessage(text, firstRootOverride);
    } else {
      sceneStore.sendMessage(text);
    }
    store.updateDraft("");
  }

  return (
    <div className="composer-dock">
      {agentMode && (
        <div className="composer-agent-target" aria-label="Agent destination">
          <span className="composer-agent-target-label">Workspace agent</span>
          <span className="composer-agent-target-preview">
            {selectedAgent ? `Selected: ${selectedAgent.title || "Agent"}` : "New agent · scratch workspace"}
          </span>
          {!selectedAgent && (
            <HarnessExecutionProfileSelect
              store={sceneStore}
              value={newAgentProfile}
              onChange={setNewAgentProfile}
              disabled={agentSending}
            />
          )}
          {composer.context.items.length > 0 && (
            <span className="composer-agent-target-note">Attachments are not sent to agents</span>
          )}
        </div>
      )}
      {!agentMode && replyTargetNode && (
        // ADR-002 Workstream 1: the "Branch from here" pick, staged via a
        // chat node's own context menu (ChatNodeView.tsx) and cleared
        // either by this button or automatically once consumed by the next
        // Send (sceneStore.ts's sendMessage).
        <div className="composer-reply-target" aria-label="Branching from">
          <span className="composer-reply-target-label">Branching from</span>
          <span className="composer-reply-target-preview">
            {replyTargetNode.content.trim().slice(0, 80) || "(empty message)"}
          </span>
          <button
            type="button"
            className="composer-reply-target-clear"
            aria-label="Cancel branching from this message"
            onClick={() => sceneStore.setReplyTargetNodeId(null)}
          >
            ×
          </button>
        </div>
      )}
      {!agentMode && isSynthesizing && (
        // ADR-002 Workstream 1 ("Synthesize Branches"): the selection staged
        // via App.tsx's Synthesize Branches shortcut, cleared either by this
        // button or automatically once consumed by the next Send
        // (sceneStore.ts's sendMessage). No per-node preview (unlike
        // "Branching from" above) - N branches' worth of previews would
        // clutter the composer for little benefit; the count is enough
        // context to confirm the right selection is staged.
        <div className="composer-synthesize-target" aria-label="Synthesizing branches">
          <span className="composer-synthesize-target-label">
            Synthesizing {synthesizeTargetNodeIds!.length} branches
          </span>
          <button
            type="button"
            className="composer-synthesize-target-clear"
            aria-label="Cancel synthesizing these branches"
            onClick={() => sceneStore.setSynthesizeTargetNodeIds(null)}
          >
            ×
          </button>
        </div>
      )}
      {composer.context.items.length > 0 && (
        // R8a: real staged attachments. Metadata only - the composer never
        // receives raw bytes/extracted text, just what StagedAttachment.
        // to_wire() sends (backend/attachments.py).
        <div className="composer-attachment-chips" aria-label="Staged attachments">
          {composer.context.items.map((item) => (
            <span key={item.id} className="composer-attachment-chip">
              <span className="composer-attachment-chip-kind">{item.contextLabel}</span>
              <span className="composer-attachment-chip-name" title={item.name}>
                {item.name}
              </span>
              {item.kind === "document" && (
                <button
                  type="button"
                  className="composer-attachment-chip-add"
                  aria-label={`Add ${item.name} to knowledge`}
                  disabled={knowledgeImport.busy}
                  onClick={() => void knowledgeImport.add(
                    item.name, () => store.addAttachmentToKnowledge(item.id),
                  )}
                >
                  Add to knowledge
                </button>
              )}
              <button
                type="button"
                className="composer-attachment-chip-remove"
                aria-label={`Remove ${item.name}`}
                onClick={() => store.removeAttachment(item.id)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <KnowledgeFileImportStatus state={knowledgeImport} />
      {agentError && <p className="composer-agent-error" role="alert">{agentError}</p>}
      <div className="composer-input-wrap">
        <textarea
          // ADR-012 stage 12.3: the skip-link's own jump target - see
          // App.tsx's ".skip-link" anchor, which lets a keyboard user bypass
          // every node on the canvas (an N-node canvas is otherwise N tab
          // stops before reaching this) in one activation.
          id="composer-message-input"
          className="composer-input"
          value={composer.draft.text}
          onChange={(e) => store.updateDraft(e.target.value)}
          onKeyDown={(e) => {
            // An IME's Enter-to-commit keystroke also reports key==="Enter",
            // so without this guard, confirming a composed character (e.g.
            // Japanese/Chinese/Korean input) sent the half-typed buffer.
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={
            agentMode
              ? "Give the workspace agent a task…"
              : isSynthesizing
                ? "Describe how to combine these branches…"
                : "Ask about this graph…"
          }
          aria-label="Message composer"
          rows={1}
          spellCheck
        />
      </div>

      {/* One toolbar inside the card: secondary tools as quiet icons on the
          left, the routing chips and the single primary action on the right.
          Every control keeps its accessible name; only the visible text was
          trimmed to what a user scans for (the model name, the level). */}
      <div className="composer-controls">
        {composer.request.state === "generating" && (
          <div className="composer-stream-preview" role="status" aria-live="polite">
            {streamText || "Thinking…"}
          </div>
        )}

        <div className="composer-controls-start">
          {/* R8a: real now - opens a native file dialog (backend/native_dialogs.py)
              and stages whatever is picked. */}
          <button
            type="button"
            className="composer-icon-button"
            disabled={!composer.capabilities.attachments || !composer.request.canSend}
            title="Attach a file (image, audio, or a text/PDF/DOCX document)"
            aria-label="Attach context"
            onClick={() => store.attachFile()}
          >
            <Icon name="attach" />
          </button>

          {typeof contextInspector === "function"
            ? contextInspector(knowledgePreference?.enabled ?? null, knowledgePreference?.nodeId)
            : contextInspector}

          <button
            type="button"
            className={`composer-icon-button composer-agent-toggle composer-knowledge-toggle${knowledgePreference?.enabled ? " active" : ""}`}
            aria-label="Use knowledge"
            aria-pressed={knowledgePreference?.enabled ?? false}
            disabled={!knowledgePreference || !composer.request.canSend || knowledgeUpdating || agentMode}
            title={!knowledgePreference
              ? (knowledgeCheckFailed ? "Could not check workspace knowledge" : "Checking workspace knowledge…")
              : knowledgePreference.override === null
                ? "Uses knowledge automatically when this workspace has documents"
                : "Knowledge retrieval overridden for this branch"}
            onClick={toggleUseKnowledge}
          >
            <Icon name="knowledge" />
            <span>Use knowledge</span>
          </button>

          <button
            type="button"
            className={`composer-icon-button composer-agent-toggle${agentMode ? " active" : ""}`}
            aria-label="Send to workspace agent"
            aria-pressed={agentMode}
            disabled={!composer.request.canSend || agentSending}
            title={selectedAgent ? "Send a task to the selected agent" : "Start a new workspace agent from this message"}
            onClick={() => {
              setAgentMode((enabled) => !enabled);
              setComparisonMode(false);
              setAgentError("");
              overlays.close();
            }}
          >
            <Icon name="agent" />
            <span>Agent</span>
          </button>

          <button
            type="button"
            className="composer-icon-button composer-compare-button"
            data-overlay-trigger="model-comparison"
            aria-haspopup="dialog"
            aria-pressed={comparisonMode}
            aria-label={comparisonMode ? `Compare models: ${selectedComparisonModels.length} selected` : "Compare models"}
            disabled={!composer.request.canSend || isSynthesizing || agentMode}
            title="Send one message to two to four models in parallel"
            onClick={() => { void openComparisonPicker(); }}
          >
            <Icon name="compare" />
            {comparisonMode && (
              <span className="composer-compare-count" aria-hidden="true">
                {selectedComparisonModels.length}
              </span>
            )}
          </button>
        </div>

        <div className="composer-rail-end">
          {/* R8a: a REAL model picker; it enables exactly when the backend
              says there is something to choose (capabilities.modelSelection)
              and writes through the same per-task assignment Settings edits.
              The provider stays in the accessible name and tooltip. */}
          <button
            type="button"
            className="composer-control composer-model-chip"
            data-overlay-trigger="model"
            aria-haspopup="dialog"
            aria-pressed={overlays.isOpen("model")}
            aria-label={`${composer.route.provider} model: ${modelLabel}`}
            disabled={!composer.capabilities.modelSelection || !composer.request.canSend}
            title={
              composer.capabilities.modelSelection
                ? `Choose the ${composer.route.provider} model for chat`
                : "Choose a model in Settings for this provider"
            }
            onClick={() => overlays.toggle("model", "popover")}
          >
            <span className="composer-control-value composer-control-value-model">{modelLabel}</span>
            <Icon name="chevron" />
          </button>

          {/* Shown only when the provider and model expose adjustable
              reasoning; an always-disabled chip was clutter. While a reply is
              in flight it stays visible but disabled, and says why. */}
          {composer.capabilities.reasoningSelection && (
            <button
              type="button"
              className="composer-control composer-reasoning-chip"
              data-overlay-trigger="reasoning"
              aria-haspopup="dialog"
              aria-pressed={overlays.isOpen("reasoning")}
              aria-label={`Reasoning: ${composer.route.reasoning.label}`}
              disabled={!composer.request.canSend}
              title={!composer.request.canSend ? "Wait for the current response to finish" : "Reasoning level"}
              onClick={() => overlays.toggle("reasoning", "popover")}
            >
              <Icon name="reasoning" />
              <span className="composer-control-value composer-control-value-reasoning">
                {composer.route.reasoning.label}
              </span>
              <Icon name="chevron" />
            </button>
          )}

          {showTokenCounter && <TokenCounter store={store} />}

          {composer.request.canCancel ? (
            // One primary slot, two states: while a response can still be
            // cancelled the slot IS the stop control - Send never renders
            // beside it as a competing (and necessarily disabled) twin.
            <button
              type="button"
              className="composer-send-button"
              onClick={() => {
                if (composer.request.id) store.cancelChatRequest(composer.request.id);
              }}
              title="Cancel response"
              aria-label="Cancel response"
            >
              <Icon name="stop" />
            </button>
          ) : (
            <button
              type="button"
              className="composer-send-button"
              disabled={!composer.draft.text.trim() || !composer.request.canSend || knowledgeUpdating || agentSending || (agentMode && (!composer.route.available || !composer.route.modelId)) || (comparisonMode && !agentMode && !isSynthesizing && !comparisonReady)}
              title={agentMode
                ? (agentSending ? "Starting agent…" : !composer.route.modelId || !composer.route.available ? "Choose an available model first" : "Send task to workspace agent")
                : comparisonMode && !comparisonReady && !isSynthesizing ? "Choose at least two comparison models" : "Send message"}
              aria-label="Send message"
              onClick={send}
            >
              <Icon name="send" />
            </button>
          )}
        </div>
      </div>

      {overlays.isOpen("model-comparison") && (
        <Suspense fallback={
          <Popover anchored name="model-comparison" label="Compare models" className="reasoning-popover model-picker-popover">
            <p role="status">Loading comparison picker…</p>
          </Popover>
        }>
          <LazyModelComparisonPicker
            comparisonMode={comparisonMode}
            comparisonOptions={comparisonOptions}
            selectedComparisonKeys={selectedComparisonKeys}
            selectedComparisonModels={selectedComparisonModels}
            comparisonLoading={comparisonLoading}
            comparisonError={comparisonError}
            comparisonKey={comparisonKey}
            setSelectedComparisonKeys={setSelectedComparisonKeys}
            onUseSingleModel={() => setComparisonMode(false)}
          />
        </Suspense>
      )}
      <Reasoning store={store} />
      {overlays.isOpen("model") && <ModelPicker key={composer.route.provider} store={store} />}
    </div>
  );
}

function ModelPicker({ store }: { store: ComposerStore }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const [query, setQuery] = useState("");
  const search = query.trim().toLowerCase();
  const options = composer.route.modelOptions;
  const matches = options.filter(({ id, label }) =>
    id.toLowerCase().includes(search) || label.toLowerCase().includes(search),
  );

  return (
    <Popover anchored name="model" label="Choose a model" className="reasoning-popover model-picker-popover">
      {options.length > 8 && (
        <input
          type="search"
          className="model-picker-search"
          aria-label="Search models"
          placeholder={`Search ${options.length} models…`}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            // Search is the keyboard's active focus target. When there is
            // exactly one match, Enter chooses it without a separate Tab.
            // IME composition's Enter must still commit the composition.
            if (event.key === "Enter" && !event.nativeEvent.isComposing && matches.length === 1) {
              event.preventDefault();
              store.selectModel(matches[0].id);
              overlays.close();
            }
          }}
        />
      )}
      <div className="model-picker-results">
        {matches.map((option) => (
          <button
            key={option.id}
            type="button"
            aria-pressed={option.id === composer.route.modelId}
            className={"reasoning-option" + (option.id === composer.route.modelId ? " active" : "")}
            onClick={() => {
              store.selectModel(option.id);
              overlays.close();
            }}
          >
            <span className="reasoning-option-label">{option.label}</span>
            {option.id === composer.route.modelId && <span aria-hidden="true">✓</span>}
          </button>
        ))}
        {matches.length === 0 && <p className="model-picker-empty" role="status">
          {search ? "No matching models." : "Choose a model in provider settings."}
        </p>}
      </div>
    </Popover>
  );
}

function Reasoning({ store }: { store: ComposerStore }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const options = useMemo(() => composer.route.reasoning.options, [composer.route.reasoning.options]);

  return (
    <Popover anchored name="reasoning" label="Choose a reasoning level" className="reasoning-popover">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className={
            "reasoning-option" + (option.id === composer.route.reasoning.level ? " active" : "")
          }
          onClick={() => {
            store.setReasoningLevel(option.id);
            overlays.close();
          }}
        >
          <span className="reasoning-option-label">{option.label}</span>
          <span className="reasoning-option-description">{option.description}</span>
        </button>
      ))}
    </Popover>
  );
}
