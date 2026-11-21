import { useEffect, useRef, useState } from "react";
import { Dialog, useOverlays } from "../overlays/overlays";
import type { ChatRerunPreview, ChatRerunScope, ChatRerunStatus, SceneStore } from "./sceneStore";
import "./ChatRerunDialog.css";

const DIALOG = "chat-rerun";

/** A repeated poll is not a UI state change; don't re-render the dialog. */
export function sameChatRerunProgress(a: ChatRerunStatus | null, b: ChatRerunStatus): boolean {
  return a !== null && a.jobId === b.jobId && a.status === b.status
    && a.completed === b.completed && a.total === b.total
    && a.currentNodeId === b.currentNodeId && a.error === b.error
    && a.cancelRequested === b.cancelRequested;
}

/** A server-owned preview: no client guess about canonical ancestry or cost. */
export function ChatRerunDialog({
  store, nodeId, onRunBoundNodes,
}: {
  store: SceneStore;
  nodeId: string | null;
  onRunBoundNodes?: (ids: string[]) => void;
}) {
  const overlays = useOverlays();
  const isOpen = overlays.isOpen(DIALOG);
  const [scope, setScope] = useState<ChatRerunScope>("this_path");
  const [loaded, setLoaded] = useState<{
    key: string; data?: ChatRerunPreview; error?: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [confirmedToken, setConfirmedToken] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<ChatRerunStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [previewRevision, setPreviewRevision] = useState(0);
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const retryButtonRef = useRef<HTMLButtonElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  // Dialog stays mounted while closed. Never reuse a previously authorized
  // preview token after the scene may have changed in the meantime.
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (wasOpen !== isOpen) {
    setWasOpen(isOpen);
    if (!isOpen) {
      setLoaded(null);
      setConfirmedToken(null);
      setError("");
      if (progress?.status !== "running") {
        setJobId(null);
        setProgress(null);
      }
    }
  }
  const previewKey = nodeId + ":" + scope;
  const preview = loaded?.key === previewKey ? loaded.data ?? null : null;
  const previewError = loaded?.key === previewKey ? loaded.error ?? "" : "";
  const busyPreview = isOpen && !!nodeId && !jobId && loaded?.key !== previewKey;
  const confirmedLarge = !!preview && confirmedToken === preview.previewToken;
  const boundNodeIds = preview?.skipped.filter((row) => row.category === "bound")
    .map((row) => row.nodeId) ?? [];

  useEffect(() => {
    if (!isOpen || !nodeId || jobId) return;
    let active = true;
    void store.previewChatRerun(nodeId, scope)
      .then((data) => { if (active) setLoaded({ key: previewKey, data }); })
      .catch((cause: unknown) => {
        if (active) setLoaded({
          key: previewKey,
          error: cause instanceof Error ? cause.message : "Could not preview replies.",
        });
      });
    return () => { active = false; };
  }, [store, isOpen, nodeId, scope, jobId, previewKey, previewRevision]);

  // Progress is deliberately not part of the persistent scene schema.
  // Poll only this open dialog; the backend job continues if it is dismissed.
  useEffect(() => {
    if (!isOpen || !jobId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const next = await store.getChatRerunStatus(jobId);
        if (!active) return;
        setError("");
        setProgress((current) => {
          // Preserve acknowledged cancellation against an older in-flight poll.
          const updated = current?.jobId === next.jobId && current.cancelRequested
            ? { ...next, cancelRequested: true } : next;
          return sameChatRerunProgress(current, updated) ? current : updated;
        });
        if (next.status === "running") timer = setTimeout(() => { void poll(); }, 250);
      } catch (cause) {
        if (active) {
          setError(cause instanceof Error ? cause.message : "Could not read re-run progress.");
          // An intermittent connection must not strand a running job in an
          // indefinitely frozen status. Retry until close/unmount.
          timer = setTimeout(() => { void poll(); }, 1000);
        }
      }
    };
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [store, isOpen, jobId]);

  const running = jobId !== null && (progress === null || progress.status === "running");
  const finished = progress !== null && progress.status !== "running";
  const currentReplyTitle = preview?.replies.find((reply) => reply.nodeId === progress?.currentNodeId)?.title;

  useEffect(() => {
    if (!isOpen || document.activeElement !== document.body) return;
    // Start and completion unmount the focused action. Keep the keyboard
    // inside the existing shared Dialog instead of leaving focus on body.
    if (running && jobId) cancelButtonRef.current?.focus();
    else if (finished) retryButtonRef.current?.focus();
  }, [isOpen, jobId, running, finished]);

  async function begin() {
    if (!nodeId || !preview || running || starting) return;
    if (preview.requiresConfirmation && !confirmedLarge) return;
    setStarting(true);
    setError("");
    try {
      const result = await store.startChatRerun(nodeId, scope, preview.previewToken, confirmedLarge);
      setJobId(result.jobId);
      setProgress({
        jobId: result.jobId, status: "running", completed: 0,
        total: preview.count, currentNodeId: null, error: "", cancelRequested: false,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start re-run.");
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!jobId || !running) return;
    try {
      const response = await store.cancelChatRerun(jobId);
      if (response.ok) {
        setProgress((current) => current ? { ...current, cancelRequested: true } : current);
        closeButtonRef.current?.focus();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not cancel re-run.");
    }
  }

  function refreshPreview() {
    setLoaded(null);
    setConfirmedToken(null);
    setError("");
    setPreviewRevision((revision) => revision + 1);
  }

  function reset() {
    setJobId(null);
    setProgress(null);
    setScope("this_path");
    refreshPreview();
  }

  function chooseScope(value: ChatRerunScope) {
    setScope(value);
    setConfirmedToken(null);
    setError("");
  }

  return (
    <Dialog name={DIALOG} title="Re-run from here" className="chat-rerun-dialog">
      <p>Only ordinary tool-free chat replies are re-run automatically. Workspace agents, Builder,
        code sandboxes, tool-executing replies, plugin actions and data-bound outputs are skipped.
        Skipped nodes and their downstream replies require manual review.</p>
      <fieldset className="chat-rerun-scopes" disabled={!!jobId || starting}>
        <legend>Replies to re-run</legend>
        <label>
          <input type="radio" name="chat-rerun-scope" value="this_path"
            checked={scope === "this_path"} onChange={() => chooseScope("this_path")} />
          This path (follow the active branch)
        </label>
        <label>
          <input type="radio" name="chat-rerun-scope" value="all_branches_below"
            checked={scope === "all_branches_below"} onChange={() => chooseScope("all_branches_below")} />
          All branches below (include forks)
        </label>
      </fieldset>

      {!jobId && <p className="chat-rerun-scope-help">
        The preview lists replies from upstream to downstream. Input-token counts are estimates;
        later replies may use different context after earlier replies finish.
      </p>}
      {busyPreview && !jobId && <p role="status">Checking replies…</p>}
      {preview && (
        <>
          <p id="chat-rerun-count">
            {preview.count} {preview.count === 1 ? "reply" : "replies"} in parent-before-child order
          </p>
          <ol className="chat-rerun-replies" aria-labelledby="chat-rerun-count">
            {preview.replies.map((reply) => (
              <li key={reply.nodeId}>
                <strong>{reply.title}</strong>
                <span className="chat-rerun-meta">
                  Model: {reply.provider && reply.model
                    ? reply.provider + " / " + reply.model
                    : reply.model || reply.provider || "Current route"}
                  {" · "}Estimated input: {reply.estimatedInputTokens.toLocaleString()} tokens
                  {" · "}Estimated input cost: {reply.estimatedInputCostUsd == null
                    ? "unavailable" : "$" + reply.estimatedInputCostUsd.toFixed(6)}
                </span>
              </li>
            ))}
          </ol>
          {preview.skipped.length > 0 && (
            <section aria-label="Skipped nodes">
              <h3 className="chat-rerun-skipped-title" id="chat-rerun-skipped-title">
                Skipped — never executed by this re-run ({preview.skipped.length})
              </h3>
              <ol className="chat-rerun-replies" aria-labelledby="chat-rerun-skipped-title">
                {preview.skipped.map((item) => (
                  <li key={item.nodeId}>
                    <strong>{item.title}</strong>
                    <span className="chat-rerun-meta">{item.reason}</span>
                  </li>
                ))}
              </ol>
              {boundNodeIds.length > 0 && onRunBoundNodes && (
                <button type="button" className="chat-rerun-run-bound"
                  onClick={() => onRunBoundNodes(boundNodeIds)}>
                  Run bound nodes
                </button>
              )}
              {boundNodeIds.length > 0 && (
                <p>Run bound nodes opens Graph Runner for a separate dependency preview and explicit approval.
                  No bound output is executed by Re-run from here.</p>
              )}
            </section>
          )}
          {preview.count === 0 && <p>No ordinary chat replies are below this message.</p>}
          {preview.requiresConfirmation && !jobId && (
            <label className="chat-rerun-confirm">
              <input type="checkbox" checked={confirmedLarge}
                onChange={(event) => setConfirmedToken(event.target.checked ? preview.previewToken : null)} />
              I confirm re-running more than 10 replies ({preview.count} total).
            </label>
          )}
        </>
      )}

      {progress && (
        <section aria-label="Re-run progress" className="chat-rerun-progress">
          <progress aria-label="Replies completed" value={progress.completed} max={Math.max(1, progress.total)} />
          <p role="status" aria-live="polite">
            {progress.status === "running"
              ? (progress.cancelRequested ? "Stopping after the current reply…" : "Re-running replies…")
              : progress.status === "completed" ? "Re-run complete."
              : progress.status === "cancelled" ? "Re-run cancelled."
              : "Re-run failed."}
            {" "}{progress.completed} of {progress.total} replies completed.
            {progress.currentNodeId && progress.status === "running"
              ? " Current reply: " + (currentReplyTitle ?? "selected reply") + "." : ""}
          </p>
          {progress.error && <p role="alert">{progress.error}</p>}
        </section>
      )}
      {previewError && <p role="alert">{previewError}</p>}
      {error && <p role="alert">{error}</p>}

      <div className="chat-rerun-actions">
        {!jobId && (
          <button type="button" onClick={() => { void begin(); }}
            disabled={starting || busyPreview || !preview || preview.count === 0
              || (preview.requiresConfirmation && !confirmedLarge)}>
            {starting ? "Starting…" : "Re-run replies"}
          </button>
        )}
        {!jobId && <button type="button" onClick={refreshPreview}>Refresh preview</button>}
        {running && (
          <button ref={cancelButtonRef} type="button" disabled={!!progress?.cancelRequested}
            onClick={() => { void cancel(); }}>
            Cancel re-run
          </button>
        )}
        {finished && <button ref={retryButtonRef} type="button" onClick={reset}>Preview another re-run</button>}
        <button ref={closeButtonRef} type="button" onClick={overlays.close}>Close</button>
      </div>
    </Dialog>
  );
}
