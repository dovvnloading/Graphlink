import { useLayoutEffect, useMemo, useRef, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover, useOverlays } from "../overlays/overlays";
import type { ComposerStore } from "./composerStore";
import { TokenCounter } from "./TokenCounter";

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

function Icon({ name }: { name: "attach" | "send" | "chevron" | "stop" }) {
  const paths: Record<string, string> = {
    attach:
      "M12 5.5 6.4 11.1a3.6 3.6 0 0 0 5.1 5.1l6-6a2.5 2.5 0 0 0-3.5-3.5l-6.1 6.1a1.35 1.35 0 0 0 1.9 1.9l5.5-5.5",
    send: "M3.5 4.6 20.5 12 3.5 19.4l2.2-6.2L15 12 5.7 10.8 3.5 4.6Z",
    chevron: "m7 10 5 5 5-5",
    stop: "M7 7h10v10H7z",
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
}: {
  store: ComposerStore;
  sceneStore: SceneStore;
  showTokenCounter?: boolean;
}) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const streamText = useSyncExternalStore(store.subscribe, store.getStreamText);
  const overlays = useOverlays();
  const inputRef = useRef<HTMLTextAreaElement>(null);

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

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.max(42, Math.min(160, input.scrollHeight))}px`;
  }, [composer.draft.text]);

  const modelLabel = composer.route.modelLabel || composer.route.modelId || "Select a model";
  const isSynthesizing = !!synthesizeTargetNodeIds && synthesizeTargetNodeIds.length > 0;

  function send() {
    const text = composer.draft.text.trim();
    if (!text || !composer.request.canSend) return;
    sceneStore.sendMessage(text);
    store.updateDraft("");
  }

  return (
    <div className="composer-dock">
      {replyTargetNode && (
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
      {isSynthesizing && (
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
      <div className="composer-input-wrap">
        <textarea
          ref={inputRef}
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
            isSynthesizing
              ? "Describe how to combine these branches…"
              : "Ask about this graph…"
          }
          aria-label="Message composer"
          rows={1}
          spellCheck
        />
      </div>

      <div className="composer-controls">
        {composer.request.state === "generating" && (
          <div className="composer-stream-preview" role="status" aria-live="polite">
            {streamText || "Thinking…"}
          </div>
        )}

        {/* R8a: real now - opens a native file dialog (backend/native_dialogs.py)
            and stages whatever is picked. Was hard-disabled with an
            "aren't available yet" title and nothing behind it. */}
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

        <button
          type="button"
          className="composer-control"
          data-overlay-trigger="reasoning"
          aria-haspopup="dialog"
          aria-pressed={overlays.isOpen("reasoning")}
          disabled={!composer.capabilities.reasoningSelection || !composer.request.canSend}
          /* R8a (UI/UX issue list finding #11): its two disabled-reason
             neighbours (Attach, Model) both explain themselves via title -
             this button had none at all, so a greyed-out Reasoning chip in
             API mode looked like just another broken control rather than a
             provider limitation. */
          title={
            !composer.capabilities.reasoningSelection
              ? "Reasoning mode is only available for local Ollama and Llama.cpp providers"
              : !composer.request.canSend
                ? "Wait for the current response to finish"
                : undefined
          }
          onClick={() => overlays.toggle("reasoning", "popover")}
        >
          <span className="control-kicker">Reasoning</span>
          <span className="control-value">{composer.route.reasoning.label}</span>
          <Icon name="chevron" />
        </button>

        {/* R8a: a REAL model picker. This was `disabled` with a "not available
            yet" title and an always-empty option list, so it rendered as a
            dropdown that could never be opened - reported, correctly, as
            "does not work at all and is locked up". It now enables exactly
            when the backend says there is something to choose
            (capabilities.modelSelection, derived from the resolved provider's
            real model list) and writes through the same per-task assignment
            the Settings > Ollama page edits. */}
        <button
          type="button"
          className="composer-control"
          data-overlay-trigger="model"
          aria-haspopup="dialog"
          aria-pressed={overlays.isOpen("model")}
          disabled={!composer.capabilities.modelSelection || !composer.request.canSend}
          title={
            composer.capabilities.modelSelection
              ? `Choose the ${composer.route.provider} model for chat`
              : "No models found - run a scan on the Settings page for this provider"
          }
          onClick={() => overlays.toggle("model", "popover")}
        >
          <span className="control-copy">
            <span className="control-kicker">{composer.route.provider}</span>
            <span className="control-value" title={modelLabel}>
              {modelLabel}
            </span>
          </span>
          <Icon name="chevron" />
        </button>

        {showTokenCounter && <TokenCounter store={store} />}

        <div className="composer-send-group">
          {composer.request.canCancel && (
            <button
              type="button"
              className="composer-icon-button composer-cancel-button"
              onClick={() => {
                if (composer.request.id) store.cancelChatRequest(composer.request.id);
              }}
              title="Cancel response"
              aria-label="Cancel response"
            >
              <Icon name="stop" />
            </button>
          )}

          <button
            type="button"
            className="composer-send-button"
            disabled={!composer.draft.text.trim() || !composer.request.canSend}
            title="Send message"
            aria-label="Send message"
            onClick={send}
          >
            <Icon name="send" />
          </button>
        </div>
      </div>

      <Reasoning store={store} />
      <ModelPicker store={store} />
    </div>
  );
}

function ModelPicker({ store }: { store: ComposerStore }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const options = useMemo(() => composer.route.modelOptions, [composer.route.modelOptions]);

  return (
    <Popover name="model" label="Choose a model" className="reasoning-popover model-picker-popover">
      {options.length === 0 ? (
        <p className="model-picker-empty">
          No models found for {composer.route.provider}. Run a scan on its Settings page.
        </p>
      ) : (
        options.map((option) => (
          <button
            key={option.id}
            type="button"
            className={"reasoning-option" + (option.id === composer.route.modelId ? " active" : "")}
            onClick={() => {
              store.selectModel(option.id);
              overlays.close();
            }}
          >
            <span className="reasoning-option-label">{option.label}</span>
          </button>
        ))
      )}
    </Popover>
  );
}

function Reasoning({ store }: { store: ComposerStore }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const options = useMemo(() => composer.route.reasoning.options, [composer.route.reasoning.options]);

  return (
    <Popover name="reasoning" label="Choose a reasoning level" className="reasoning-popover">
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
