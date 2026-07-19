import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ComposerState, initialComposerState } from "./bridgeTypes";
import { ComposerBridge, createComposerBridge } from "./bridge";
import { isBusy, requestLabel } from "./state";
import { applyThemeCssVariables } from "./theme";

const LARGE_PASTE_CHAR_THRESHOLD = 1400;
const LARGE_PASTE_LINE_THRESHOLD = 24;

function Icon({ name }: { name: "attach" | "send" | "stop" | "chevron" }) {
  const paths: Record<string, string> = {
    attach: "M12 5.5 6.4 11.1a3.6 3.6 0 0 0 5.1 5.1l6-6a2.5 2.5 0 0 0-3.5-3.5l-6.1 6.1a1.35 1.35 0 0 0 1.9 1.9l5.5-5.5",
    send: "M3.5 4.6 20.5 12 3.5 19.4l2.2-6.2L15 12 5.7 10.8 3.5 4.6Z",
    stop: "M6.5 6.5h11v11h-11z",
    chevron: "m7 10 5 5 5-5",
  };
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
      <path d={paths[name]} />
    </svg>
  );
}

function ComposerApp() {
  const [state, setState] = useState<ComposerState>(initialComposerState);
  const bridgeRef = useRef<ComposerBridge | null>(null);
  const shellRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isRequestBusy = isBusy(state);
  const attachmentCount = state.context.items.length;

  useEffect(() => {
    const bridge = createComposerBridge(setState);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  // Applies the active theme's --gl-* custom properties to the document root
  // on every snapshot, so an in-app theme change re-styles composer without a
  // reload. First paint already has a value for every property (the host
  // injects a build-time :root block before this ever runs) - this is what
  // makes a LIVE theme switch visible; it does not do the initial styling.
  useEffect(() => {
    applyThemeCssVariables(document.documentElement, state.theme.cssVariables);
  }, [state.theme.cssVariables]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;

    const reportHeight = () => {
      bridgeRef.current?.resize(Math.ceil(shell.getBoundingClientRect().height));
    };
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(reportHeight);
    observer?.observe(shell);
    reportHeight();
    return () => observer?.disconnect();
  }, []);

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.max(42, Math.min(160, input.scrollHeight))}px`;
  }, [state.draft.text]);

  const canSend = state.request.canSend && !isRequestBusy;
  const routeDetail = useMemo(() => {
    if (state.route.modelId) return state.route.label + " \u00b7 " + state.route.modelId;
    return state.route.label;
  }, [state.route.label, state.route.modelId, state.route.modelLabel]);
  const modelDisplayLabel = state.route.modelLabel || state.route.modelId || "Select a model";

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

  function onPaste(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    if (isRequestBusy || event.clipboardData.files.length > 0) return;

    const pastedText = event.clipboardData.getData("text/plain");
    const isLargePaste =
      pastedText.length >= LARGE_PASTE_CHAR_THRESHOLD ||
      pastedText.split("\n").length >= LARGE_PASTE_LINE_THRESHOLD;
    if (!pastedText.trim() || !isLargePaste) return;

    event.preventDefault();
    bridgeRef.current?.stageTextAttachment(pastedText);
  }

  return (
    <main
      ref={shellRef}
      className="composer-shell"
      data-request-state={state.request.state}
    >
      <div className="input-wrap">
        <textarea
          ref={inputRef}
          value={state.draft.text}
          onChange={(event) => bridgeRef.current?.updateDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={"Ask about this graph\u2026"}
          aria-label="Message composer"
          rows={1}
          spellCheck
          disabled={isRequestBusy}
        />
      </div>

      <footer className="composer-footer">
        <div className="footer-left">
          <div className="attachment-control">
            <button
              className="attach-button"
              type="button"
              onClick={() => bridgeRef.current?.requestAttachment()}
              disabled={!state.capabilities.attachments || isRequestBusy}
              aria-label="Attach context"
              title="Attach context"
            >
              <Icon name="attach" />
            </button>
            {attachmentCount > 0 && (
              <button
                className="attachment-count"
                type="button"
                onClick={() => bridgeRef.current?.reviewContext()}
                disabled={!state.capabilities.contextReview || isRequestBusy}
                aria-haspopup="dialog"
                aria-label={`Review ${attachmentCount} attached ${attachmentCount === 1 ? "item" : "items"}`}
                title={`Review ${attachmentCount} attached ${attachmentCount === 1 ? "item" : "items"}`}
              >
                {attachmentCount}
              </button>
            )}
          </div>
          {state.draft.restored && <span className="restored-pill">Restored draft</span>}
        </div>
        <div className="footer-right">
          <button
            className="composer-control reasoning-control"
            type="button"
            disabled={!state.capabilities.reasoningSelection || isRequestBusy}
            aria-haspopup="dialog"
            onClick={() => bridgeRef.current?.openReasoningSelector()}
            title="Choose reasoning level"
          >
            <span className="control-kicker">Reasoning</span>
            <span className="control-value">{state.route.reasoning.label}</span>
            <Icon name="chevron" />
          </button>
          <button
            className="composer-control model-control"
            type="button"
            disabled={!state.capabilities.modelSelection || !state.route.canChange || isRequestBusy}
            aria-haspopup="dialog"
            onClick={() => bridgeRef.current?.openModelSelector()}
            title={routeDetail}
          >
            <span className="control-copy">
              <span className="control-kicker">{state.route.provider}</span>
              <span className="control-value" title={modelDisplayLabel}>{modelDisplayLabel}</span>
            </span>
            <Icon name="chevron" />
          </button>
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
