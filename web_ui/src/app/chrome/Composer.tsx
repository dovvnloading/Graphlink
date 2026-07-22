import { useLayoutEffect, useMemo, useRef, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover, useOverlays } from "../overlays/overlays";
import type { ComposerStore } from "./composerStore";

/**
 * The composer dock (Qt-removal plan R2.3/R3.3) - ComposerApp's SPA
 * successor.
 *
 * Real here: draft text editing, reasoning-level selection (a stored
 * preference popover, reusing the overlay system rather than a dedicated
 * picker island), and (R3.3) Send - a real user ChatNode via
 * sceneStore.sendMessage. The assistant's reply is NOT generated here (see
 * backend/canvas.py's send_message docstring): the backend gives an honest
 * "lands in R4" notice over the existing notification topic instead of a
 * fake response. Visibly deferred, per backend/composer.py's capability
 * flags: attach (file-staging pipeline is an R4 concern), context review
 * (nothing to review until attachments exist), model/provider selection
 * (R4 - needs real provider wiring). Each renders disabled with a title
 * naming its phase, exactly the app bar's Save/provider-select precedent.
 *
 * Theme is NOT read from this payload (see backend/composer.py's docstring
 * for why) - the SPA's tokens are already global CSS.
 */

function Icon({ name }: { name: "attach" | "send" | "chevron" }) {
  const paths: Record<string, string> = {
    attach:
      "M12 5.5 6.4 11.1a3.6 3.6 0 0 0 5.1 5.1l6-6a2.5 2.5 0 0 0-3.5-3.5l-6.1 6.1a1.35 1.35 0 0 0 1.9 1.9l5.5-5.5",
    send: "M3.5 4.6 20.5 12 3.5 19.4l2.2-6.2L15 12 5.7 10.8 3.5 4.6Z",
    chevron: "m7 10 5 5 5-5",
  };
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
      <path d={paths[name]} />
    </svg>
  );
}

export function Composer({ store, sceneStore }: { store: ComposerStore; sceneStore: SceneStore }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.max(42, Math.min(160, input.scrollHeight))}px`;
  }, [composer.draft.text]);

  const modelLabel = composer.route.modelLabel || composer.route.modelId || "Select a model";

  function send() {
    const text = composer.draft.text.trim();
    if (!text) return;
    sceneStore.sendMessage(text);
    store.updateDraft("");
  }

  return (
    <div className="composer-dock">
      <div className="composer-input-wrap">
        <textarea
          ref={inputRef}
          className="composer-input"
          value={composer.draft.text}
          onChange={(e) => store.updateDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask about this graph…"
          aria-label="Message composer"
          rows={1}
          spellCheck
        />
      </div>

      <div className="composer-controls">
        <button
          type="button"
          className="composer-icon-button"
          disabled
          title="Attachments land in R4 (file-staging pipeline)"
          aria-label="Attach context"
        >
          <Icon name="attach" />
        </button>

        <button
          type="button"
          className="composer-control"
          data-overlay-trigger="reasoning"
          aria-haspopup="dialog"
          aria-pressed={overlays.isOpen("reasoning")}
          onClick={() => overlays.toggle("reasoning", "popover")}
        >
          <span className="control-kicker">Reasoning</span>
          <span className="control-value">{composer.route.reasoning.label}</span>
          <Icon name="chevron" />
        </button>

        <button
          type="button"
          className="composer-control"
          disabled
          title="Model/provider selection lands in R4"
        >
          <span className="control-copy">
            <span className="control-kicker">{composer.route.provider}</span>
            <span className="control-value" title={modelLabel}>
              {modelLabel}
            </span>
          </span>
          <Icon name="chevron" />
        </button>

        <button
          type="button"
          className="composer-send-button"
          disabled={!composer.draft.text.trim()}
          title="Sends a real message; the AI response lands in R4 (agent layer)"
          aria-label="Send message"
          onClick={send}
        >
          <Icon name="send" />
        </button>
      </div>

      <Reasoning store={store} />
    </div>
  );
}

function Reasoning({ store }: { store: ComposerStore }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const options = useMemo(() => composer.route.reasoning.options, [composer.route.reasoning.options]);

  return (
    <Popover name="reasoning" className="reasoning-popover">
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
