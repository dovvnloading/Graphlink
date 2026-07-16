import { useEffect, useMemo, useRef, useState } from "react";
import { ComposerState, initialComposerState } from "./bridgeTypes";
import { ComposerBridge, createComposerBridge } from "./bridge";
import { contextSummary, isBusy, requestLabel } from "./state";

function Icon({ name }: { name: "attach" | "send" | "stop" | "chevron" | "close" }) {
  const paths: Record<string, string> = {
    attach: "M12 5.5 6.4 11.1a3.6 3.6 0 0 0 5.1 5.1l6-6a2.5 2.5 0 0 0-3.5-3.5l-6.1 6.1a1.35 1.35 0 0 0 1.9 1.9l5.5-5.5",
    send: "M3.5 4.6 20.5 12 3.5 19.4l2.2-6.2L15 12 5.7 10.8 3.5 4.6Z",
    stop: "M6.5 6.5h11v11h-11z",
    chevron: "m7 10 5 5 5-5",
    close: "m7 7 10 10M17 7 7 17",
  };
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
      <path d={paths[name]} />
    </svg>
  );
}

function ComposerApp() {
  const [state, setState] = useState<ComposerState>(initialComposerState);
  const [reviewOpen, setReviewOpen] = useState(false);
  const bridgeRef = useRef<ComposerBridge | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isRequestBusy = isBusy(state);

  useEffect(() => {
    const bridge = createComposerBridge(setState);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const input = inputRef.current;
    if (!input || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const height = Math.round(entry.contentRect.height + 188);
      bridgeRef.current?.resize(Math.max(220, Math.min(520, height)));
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, []);

  const nodeLabel = state.context.anchor?.label || "New graph request";
  const canSend = state.request.canSend && !isRequestBusy;
  const routeDetail = useMemo(() => {
    if (state.route.modelId) return state.route.label + " · " + state.route.modelId;
    return state.route.label;
  }, [state.route.label, state.route.modelId]);

  function submit() {
    if (isRequestBusy) {
      bridgeRef.current?.cancel(state.request.id || undefined);
    } else if (canSend) {
      bridgeRef.current?.send();
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    const sendOnEnter =
      state.draft.sendMode === "enter_to_send" && event.key === "Enter" && !event.shiftKey;
    const sendOnCtrlEnter =
      state.draft.sendMode === "ctrl_enter_to_send" &&
      event.key === "Enter" &&
      (event.ctrlKey || event.metaKey);
    if (sendOnEnter || sendOnCtrlEnter) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <main className="composer-shell" data-request-state={state.request.state}>
      <header className="composer-header">
        <div className="context-heading">
          <span className="eyebrow">Graph context</span>
          <span className="context-title">{nodeLabel}</span>
        </div>
        <button
          className="quiet-button"
          type="button"
          onClick={() => {
            setReviewOpen((open) => !open);
            bridgeRef.current?.reviewContext();
          }}
          disabled={!state.capabilities.contextReview || !state.context.reviewAvailable}
          aria-expanded={reviewOpen}
        >
          Review context
          <Icon name="chevron" />
        </button>
      </header>

      {reviewOpen && (
        <section className="context-popover" aria-label="Context review">
          <div className="popover-heading">
            <span>Included context</span>
            <button
              className="icon-button"
              type="button"
              onClick={() => setReviewOpen(false)}
              aria-label="Close context review"
            >
              <Icon name="close" />
            </button>
          </div>
          {state.context.anchor && (
            <div className="context-row">
              <span className="context-kind">{state.context.anchor.type}</span>
              <span>{state.context.anchor.label}</span>
            </div>
          )}
          {state.context.items.map((item) => (
            <div className="context-row" key={item.id}>
              <span className="context-kind">{item.kind}</span>
              <span className="context-name">{item.name}</span>
              <button
                className="remove-context"
                type="button"
                onClick={() => bridgeRef.current?.removeContextItem(item.id)}
              >
                Remove
              </button>
            </div>
          ))}
          <div className="context-total">
            Estimated context · {state.context.totalTokens.toLocaleString()} tokens
          </div>
        </section>
      )}

      <div className="input-wrap">
        <textarea
          ref={inputRef}
          value={state.draft.text}
          onChange={(event) => bridgeRef.current?.updateDraft(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about this graph…"
          aria-label="Message composer"
          rows={1}
          spellCheck
          disabled={isRequestBusy}
        />
      </div>

      <footer className="composer-footer">
        <div className="footer-left">
          <button
            className="attach-button"
            type="button"
            onClick={() => bridgeRef.current?.requestAttachment()}
            disabled={!state.capabilities.attachments || isRequestBusy}
            aria-label="Attach context"
          >
            <Icon name="attach" />
          </button>
          <span className="context-summary">{contextSummary(state)}</span>
          {state.draft.restored && <span className="restored-pill">Restored draft</span>}
        </div>
        <div className="footer-right">
          <span className="route-status" title={routeDetail}>
            <span className={"status-dot " + (state.route.available ? "ready" : "offline")} />
            {state.route.label}
          </span>
          <button
            className={"send-button " + (isRequestBusy ? "cancel" : "")}
            type="button"
            onClick={submit}
            disabled={!isRequestBusy && !canSend}
            aria-label={isRequestBusy ? "Cancel response" : "Send message"}
            title={isRequestBusy ? "Cancel response" : "Send message"}
          >
            <Icon name={isRequestBusy ? "stop" : "send"} />
          </button>
        </div>
      </footer>

      {!!requestLabel(state) && (
        <div className={"request-status " + state.request.state} role="status">
          <span>{requestLabel(state)}</span>
          {state.request.canRetry && (
            <button type="button" onClick={() => bridgeRef.current?.send()}>
              Retry
            </button>
          )}
        </div>
      )}
    </main>
  );
}

export default ComposerApp;
