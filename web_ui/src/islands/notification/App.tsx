import { useEffect, useRef, useState } from "react";
import { NotificationState, initialNotificationState } from "./bridgeTypes";
import { BridgeRejection, NotificationBridge, createNotificationBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

type MsgType = NotificationState["msgType"];

// Titles match the old widget's TYPE_STYLES[*]["title"] verbatim.
const TYPE_META: Record<MsgType, { title: string }> = {
  info: { title: "Notice" },
  success: { title: "Success" },
  warning: { title: "Warning" },
  error: { title: "Action Needed" },
};

// Matches the old widget's copy_feedback_timer duration.
const COPY_FEEDBACK_MS = 1600;

function Icon({ name }: { name: MsgType | "close" }) {
  const paths: Record<string, string> = {
    info: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 5.2a1.3 1.3 0 1 1 0 2.6 1.3 1.3 0 0 1 0-2.6ZM13.25 17h-2.5v-7h2.5v7Z",
    success: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm-1.15 14.15-4-4 1.4-1.4 2.6 2.6 5.6-5.6 1.4 1.4-7 7Z",
    warning: "M12 2 1 21h22L12 2Zm0 6.3 1.05 6.45h-2.1L12 8.3Zm0 8.85a1.25 1.25 0 1 1 0 2.5 1.25 1.25 0 0 1 0-2.5Z",
    error: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm1.1 14.5h-2.2v-2.2h2.2v2.2Zm0-4.4h-2.2V6.7h2.2v5.4Z",
    close: "m6.4 5 12.6 12.6-1.4 1.4L5 6.4 6.4 5Zm12.6 1.4L6.4 19 5 17.6 17.6 5l1.4 1.4Z",
  };
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="notification-icon">
      <path d={paths[name]} />
    </svg>
  );
}

function App() {
  const [state, setState] = useState<NotificationState>(initialNotificationState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [copied, setCopied] = useState(false);
  // Resets "Copied" the moment a NEW Python-published state arrives, even
  // mid-feedback - done during render (React's documented "adjusting state
  // when a prop changes" pattern), not in an effect, since setState directly
  // inside an effect body triggers an avoidable cascading extra render.
  const [copyResetRevision, setCopyResetRevision] = useState(state.revision);
  if (copyResetRevision !== state.revision) {
    setCopyResetRevision(state.revision);
    setCopied(false);
  }
  const bridgeRef = useRef<NotificationBridge | null>(null);
  const shellRef = useRef<HTMLElement>(null);
  const copyTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    const bridge = createNotificationBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  // Keyed on `rejection`, not `[]` - see ComposerApp.tsx's identical effect
  // for the full rationale (a reject-then-recover cycle mounts a new <main>).
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
  }, [rejection]);

  useEffect(() => {
    return () => window.clearTimeout(copyTimerRef.current);
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Notifications unavailable"
        rejection={rejection}
        className="notification-shell notification-error"
      />
    );
  }

  function copyDetails() {
    bridgeRef.current?.copyDetails();
    setCopied(true);
    window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => setCopied(false), COPY_FEEDBACK_MS);
  }

  function dismiss() {
    bridgeRef.current?.dismiss();
  }

  return (
    <main
      ref={shellRef}
      className="notification-shell"
      data-msg-type={state.msgType}
      style={{ display: state.visible ? undefined : "none" }}
      role="status"
      aria-live="polite"
    >
      <div className="notification-header">
        <span className="notification-status-icon">
          <Icon name={state.msgType} />
        </span>
        <span className="notification-status-label">{TYPE_META[state.msgType].title}</span>
        <button
          className="notification-close-button"
          type="button"
          onClick={dismiss}
          aria-label="Dismiss notification"
          title="Dismiss notification"
        >
          <Icon name="close" />
        </button>
      </div>

      <p className="notification-message">{state.message}</p>

      <div className="notification-footer">
        <button className="notification-dismiss-button" type="button" onClick={dismiss}>
          Dismiss
        </button>
        <button className="notification-copy-button" type="button" onClick={copyDetails}>
          {copied ? "Copied" : "Copy details"}
        </button>
      </div>
    </main>
  );
}

export default App;
