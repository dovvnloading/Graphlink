import { lazy, Suspense, useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { ChatKnowledgePreference, SceneStore } from "../canvas/sceneStore";
import { KnowledgeFileImportStatus, useKnowledgeFileImport } from "../knowledgeFileImport";
import type { StateListener } from "../../lib/ws/transport";
import { Popover, useOverlays } from "../overlays/overlays";
import type { ComparisonModel, ComposerStore } from "./composerStore";
import { TokenCounter } from "./TokenCounter";
import { Icon } from "../ui/icons/Icon";
import type { IconName } from "../ui/icons/registry";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { ToggleButton } from "../ui/ToggleButton";
import { MenuButton, MenuItem } from "../ui/Menu";
import { Spinner } from "../ui/Spinner";
import { Tooltip } from "../ui/Tooltip";
import "./Composer.css";
import { ComposerModelMenu } from "./ModelPicker";
import { useModelCapabilityPreview } from "../context/ModelCapabilityContext";
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

const replyIcons: Record<string, IconName> = {
  chat: "node-user", user: "node-user", assistant: "node-assistant", thinking: "node-thinking",
  code: "node-code", code_sandbox: "node-runner", document: "node-document",
  html: "node-html", image: "node-image", conversation: "node-conversation",
  web_research: "node-web", artifact: "node-artifact", gitlink: "node-git",
  code_review: "node-review", note: "node-note", model: "node-model",
  scientific: "node-science", chart: "node-chart", table: "node-table",
  plan: "node-plan", harness: "node-agent", frame: "node-frame",
};

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
  const modelCapabilities = useModelCapabilityPreview();
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
  const selectedNode = scene.nodes.find((n) => n.id === selectedNodeId);
  const effectiveReplyTarget = replyTargetNode ?? selectedNode;
  const replyPreview = effectiveReplyTarget
    ? (effectiveReplyTarget.content?.trim() || effectiveReplyTarget.title?.trim() || "(empty message)")
    : "";
  const clearReplyTarget = () => {
    if (replyTargetNode) sceneStore.setReplyTargetNodeId(null);
    else sceneStore.setSelectedNodeId(null);
  };

  // A fresh scene's initial snapshot may have the SAME revision (0) as the
  // placeholder. Track the snapshot itself so a lookup rejected before the
  // socket opens is retried on load, as well as on later scene updates.
  // Library imports/removals do not change the scene, so they have their own
  // small versioned topic. Both signals invalidate in-flight lookups.
  const [knowledgeLibraryRevision, setKnowledgeLibraryRevision] = useState<number | null>(null);
  useEffect(() => sceneStore.subscribeKnowledgeLibrary((payload) => {
    if (typeof payload.revision === "number") setKnowledgeLibraryRevision(payload.revision);
  }), [sceneStore]);

  const knowledgeTargetNodeId = effectiveReplyTarget?.id ?? null;
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
  // Only describe the exact selected route. A pending/late lookup must not
  // warn about the model that was selected before this one.
  const currentModelCapabilities = modelCapabilities !== null
    && modelCapabilities.provider === composer.route.provider
    && modelCapabilities.modelId === composer.route.modelId ? modelCapabilities : null;
  const isSynthesizing = !!synthesizeTargetNodeIds && synthesizeTargetNodeIds.length > 0;
  const hasUsableModel = comparisonMode && !agentMode && !isSynthesizing
    ? comparisonReady : composer.route.available && !!composer.route.modelId;
  const sendDisabled = !composer.draft.text.trim() || !composer.request.canSend ||
    !hasUsableModel || knowledgeUpdating || agentSending;
  const sendDisabledReason = !composer.draft.text.trim()
    ? "Type a message" : !hasUsableModel
      ? "Choose a model first" : "Wait for the current response to finish";

  function send() {
    const text = composer.draft.text.trim();
    if (!text || !composer.request.canSend || knowledgeUpdating) return;
    if (!hasUsableModel) return;
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
        if (effectiveReplyTarget?.id) clearReplyTarget();
        if (store.getComposer().draft.text === text) store.updateDraft("");
      };
      const firstRootOverride = knowledgePreference?.nodeId === null ? knowledgePreference.override : null;
      if (firstRootOverride !== null && firstRootOverride !== undefined) {
        store.sendModelComparison(text, models, effectiveReplyTarget?.id ?? null, onSettled, firstRootOverride);
      } else {
        store.sendModelComparison(text, models, effectiveReplyTarget?.id ?? null, onSettled);
      }
      return;
    }
    // Stage a selected canvas node through the existing branch-send path.
    if (!isSynthesizing && !replyTargetNode && effectiveReplyTarget) {
      sceneStore.setReplyTargetNodeId(effectiveReplyTarget.id);
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
      {!agentMode && !isSynthesizing && effectiveReplyTarget && (
        <div className="composer-reply-target" aria-label="Replying to">
          <span className="composer-reply-target-label">Replying to</span>
          <Icon name={replyIcons[effectiveReplyTarget.kind] ?? "node-conversation"} size="sm" />
          <span className="composer-reply-target-preview">
            {effectiveReplyTarget.title?.trim() || replyPreview}
          </span>
          <IconButton icon="close" size="sm" label="Clear reply target" onClick={clearReplyTarget} />
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
          <IconButton icon="close" size="sm" label="Cancel synthesizing these branches"
            onClick={() => sceneStore.setSynthesizeTargetNodeIds(null)} />
        </div>
      )}
      {composer.context.items.length > 0 && (
        // R8a: real staged attachments. Metadata only - the composer never
        // receives raw bytes/extracted text, just what StagedAttachment.
        // to_wire() sends (backend/attachments.py).
        <div className="composer-attachment-chips" aria-label="Staged attachments">
          {composer.context.items.map((item) => (
            <span key={item.id} className="composer-attachment-chip">
              <Icon name="file" size="sm" />
              <span className="composer-attachment-chip-kind">{item.contextLabel}</span>
              <span className="composer-attachment-chip-name">{item.name}</span>
              {item.kind === "document" && (
                <Button size="sm" variant="ghost" disabled={knowledgeImport.busy}
                  onClick={() => void knowledgeImport.add(
                    item.name, () => store.addAttachmentToKnowledge(item.id),
                  )} aria-label={`Add ${item.name} to knowledge`}>
                  Add to knowledge
                </Button>
              )}
              <IconButton icon="close" size="sm" label={`Remove ${item.name}`}
                onClick={() => store.removeAttachment(item.id)} />
            </span>
          ))}
        </div>
      )}
      {agentMode && currentModelCapabilities?.tools === false && (
        <p className="composer-model-capability-warning" role="status">
          {modelLabel} does not support tool calling.{" "}
          {currentModelCapabilities.suggestedToolModel
            ? `Try installed Ollama model ${currentModelCapabilities.suggestedToolModel.modelId} (estimated to fit available system RAM) for the Agent task.`
            : "Choose an installed tool-capable model before sending this Agent task."}
          {" "}The Agent may still use its constrained JSON tool protocol when supported.
        </p>
      )}
      {!agentMode && composer.context.items.some((item) => item.kind === "image") &&
        currentModelCapabilities?.vision === false && (
          <p className="composer-model-capability-warning" role="status">
            {modelLabel} does not support image input. Choose a vision-capable model before sending these image attachments.
          </p>
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
                : effectiveReplyTarget
                  ? `Reply to “${replyPreview.slice(0, 40)}”…`
                  : scene.nodes.length === 0
                    ? "Ask anything to start a graph…"
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
            <Spinner size={14} decorative />
            <span>{streamText || "Thinking…"}</span>
          </div>
        )}
        <div className="composer-controls-start">
          <IconButton icon="attach" label="Attach context"
            disabled={!composer.capabilities.attachments || !composer.request.canSend}
            onClick={() => store.attachFile()} />
          {typeof contextInspector === "function"
            ? contextInspector(knowledgePreference?.enabled ?? null, knowledgePreference?.nodeId)
            : contextInspector}
          {!knowledgePreference?.hasDocuments ? (
            <Tooltip label={knowledgeCheckFailed ? "Could not check workspace knowledge"
              : knowledgeResolved ? "Nothing is indexed yet" : "Checking indexed knowledge…"} placement="top">
              <span className="composer-disabled-control" tabIndex={0}>
                <ToggleButton icon="knowledge" label="Use knowledge" size="sm" pressed={false} disabled />
              </span>
            </Tooltip>
          ) : (
            <ToggleButton icon="knowledge" label="Use knowledge" size="sm"
              pressed={knowledgePreference.enabled}
              disabled={!composer.request.canSend || knowledgeUpdating || agentMode}
              onPressedChange={toggleUseKnowledge} />
          )}
          <ToggleButton icon="agent" label="Agent" aria-label="Send to workspace agent" size="sm"
            pressed={agentMode} disabled={!composer.request.canSend || agentSending}
            onPressedChange={(enabled) => {
              setAgentMode(enabled);
              setComparisonMode(false);
              setAgentError("");
              overlays.close();
            }} />
          <IconButton icon="compare" label={comparisonMode
            ? `Compare models: ${selectedComparisonModels.length} selected` : "Compare models"}
            data-overlay-trigger="model-comparison" aria-pressed={comparisonMode}
            disabled={!composer.request.canSend || isSynthesizing || agentMode}
            onClick={() => { void openComparisonPicker(); }} />
          {comparisonMode && <span className="composer-compare-count">{selectedComparisonModels.length}</span>}
        </div>
        <div className="composer-rail-end">
          <ComposerModelMenu store={store} disabled={composer.request.state === "generating"} />
          {composer.capabilities.reasoningSelection && <Reasoning store={store} />}
          {showTokenCounter && <TokenCounter store={store} />}
          {composer.request.canCancel ? (
            <IconButton icon="stop" label="Stop generating" size="lg" variant="primary"
              className="composer-send-button"
              onClick={() => {
                if (composer.request.id) store.cancelChatRequest(composer.request.id);
              }} />
          ) : sendDisabled ? (
            <Tooltip label={sendDisabledReason} placement="top">
              <span className="composer-disabled-control" tabIndex={0}>
                <IconButton icon="arrow-up" label="Send message" size="lg"
                  variant="ghost" className="composer-send-button" disabled />
              </span>
            </Tooltip>
          ) : (
            <IconButton icon="arrow-up" label="Send message" size="lg"
              variant="primary" className="composer-send-button"
              onClick={send} />
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

    </div>
  );
}

function Reasoning({ store }: { store: ComposerStore }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const trigger = (
    <MenuButton label={composer.route.reasoning.label} data-overlay-trigger="reasoning"
      className="composer-menu-button"
      aria-label={`Reasoning: ${composer.route.reasoning.label}`}
      disabled={!composer.request.canSend} menuLabel="Choose a reasoning level">
      {composer.route.reasoning.options.map((option) => (
        <MenuItem key={option.id} label={option.label} description={option.description}
          kind="radio" checked={option.id === composer.route.reasoning.level}
          onSelect={() => store.setReasoningLevel(option.id)} />
      ))}
    </MenuButton>
  );
  return (
    <span className="composer-reasoning-menu">
      <Icon name="gauge" size="sm" />
      {composer.request.canSend ? trigger : (
        <Tooltip label="Wait for the current response to finish" placement="top">
          <span className="composer-disabled-control" tabIndex={0}>{trigger}</span>
        </Tooltip>
      )}
    </span>
  );
}
