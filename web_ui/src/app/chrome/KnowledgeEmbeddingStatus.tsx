import { useEffect, useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";

interface EmbeddingJob {
  jobId: string;
  workspaceId: number | null;
  source: string;
  model: string;
  status: "queued" | "running" | "completed" | "cancelled" | "failed";
  chunksTotal: number;
  chunksEmbedded: number;
  chunksRemaining: number;
  cancelRequested: boolean;
  error: string;
}

interface EmbeddingSnapshot {
  workspaceId: number | null;
  jobs: EmbeddingJob[];
}

interface EmbeddingChoice {
  source: string;
  model: string;
}

function isWorkspaceId(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isInteger(value));
}

function isEmbeddingJob(value: unknown): value is EmbeddingJob {
  if (!value || typeof value !== "object") return false;
  const job = value as Record<string, unknown>;
  return typeof job.jobId === "string" &&
    isWorkspaceId(job.workspaceId) &&
    typeof job.source === "string" &&
    typeof job.model === "string" &&
    (job.status === "queued" || job.status === "running" ||
      job.status === "completed" || job.status === "cancelled" || job.status === "failed") &&
    typeof job.chunksTotal === "number" && Number.isFinite(job.chunksTotal) &&
    typeof job.chunksEmbedded === "number" && Number.isFinite(job.chunksEmbedded) &&
    typeof job.chunksRemaining === "number" && Number.isFinite(job.chunksRemaining) &&
    typeof job.cancelRequested === "boolean" &&
    typeof job.error === "string";
}

function readEmbeddingSnapshot(value: unknown): EmbeddingSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  if (!isWorkspaceId(record.workspaceId) || !Array.isArray(record.jobs)) return null;
  return { workspaceId: record.workspaceId, jobs: record.jobs.filter(isEmbeddingJob) };
}

function isActiveEmbedding(job: EmbeddingJob): boolean {
  return job.status === "queued" || job.status === "running";
}

function sourceLabel(source: string): string {
  if (source === "ollama") return "Ollama (local)";
  if (source === "cloud") return "Cloud API";
  if (source === "off") return "Off";
  return source;
}

/**
 * Uses the live topic (including its initial snapshot/reconnect replay) for
 * progress. The read-only intent rechecks workspace context on every open;
 * a late request must never overwrite newer progress from the event stream.
 */
export function KnowledgeEmbeddingStatus({
  transport,
  open,
}: {
  transport: WsTransport;
  open: boolean;
}) {
  const [snapshot, setSnapshot] = useState<EmbeddingSnapshot | null>(null);
  const [choice, setChoice] = useState<EmbeddingChoice | null>(null);
  const [pendingAction, setPendingAction] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const latestEvent = useRef(0);
  const actionInFlight = useRef(false);

  useEffect(() => transport.subscribe("knowledge-embedding", (payload) => {
    const next = readEmbeddingSnapshot(payload);
    if (next) {
      latestEvent.current += 1;
      setSnapshot(next);
      setActionError(null);
    }
  }), [transport]);

  useEffect(() => transport.subscribe("app-settings", (payload) => {
    if (typeof payload.knowledgeEmbeddingSource === "string" &&
        typeof payload.knowledgeEmbeddingModel === "string") {
      setChoice({
        source: payload.knowledgeEmbeddingSource,
        model: payload.knowledgeEmbeddingModel,
      });
    }
  }), [transport]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const eventAtRequest = latestEvent.current;
    void transport.request("knowledge", "getEmbeddingJobs", [])
      .then((value) => {
        // The topic may have delivered a fresher job batch while this read
        // was in flight. Never roll progress or workspace context backwards.
        if (cancelled || latestEvent.current !== eventAtRequest) return;
        const next = readEmbeddingSnapshot(value);
        if (next) {
          setSnapshot(next);
          setActionError(null);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled && latestEvent.current === eventAtRequest) {
          setActionError(cause instanceof Error ? cause.message : "Could not load embedding status.");
        }
      });
    return () => { cancelled = true; };
  }, [open, transport]);

  const matchingJobs = snapshot?.jobs.filter((job) =>
    job.workspaceId === snapshot.workspaceId &&
    choice !== null &&
    job.source === choice.source &&
    job.model === choice.model
  ) ?? [];
  const job = matchingJobs.at(-1);
  const lastError = [...matchingJobs].reverse().find((row) => row.error)?.error;
  const active = job !== undefined && isActiveEmbedding(job);
  const resume = job?.status === "cancelled" || job?.status === "failed";
  const canRun = choice !== null && choice.source !== "off" && snapshot !== null;

  async function perform(action: "resumeEmbedding" | "cancelEmbedding", args: unknown[]) {
    // A synchronously held guard also blocks double Enter presses before
    // React has committed the disabled state.
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setPendingAction(true);
    setActionError(null);
    try {
      await transport.request("knowledge", action, args);
      // The backend emits a new knowledge-embedding snapshot for both actions.
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Embedding action failed.");
    } finally {
      actionInFlight.current = false;
      setPendingAction(false);
    }
  }

  return (
    <section className="knowledge-library-section" aria-label="Knowledge embeddings">
      <div className="knowledge-library-heading">
        <h2 className="knowledge-section-title">Knowledge embeddings</h2>
        <div className="knowledge-library-actions">
          {active ? (
            <button
              type="button"
              className="knowledge-search-button"
              disabled={pendingAction || job.cancelRequested}
              onClick={() => void perform("cancelEmbedding", [job.jobId])}
            >
              {job.cancelRequested ? "Cancelling embeddings…" : "Cancel embeddings"}
            </button>
          ) : (
            <button
              type="button"
              className="knowledge-search-button"
              disabled={!canRun || pendingAction}
              onClick={() => void perform("resumeEmbedding", [])}
            >
              {resume ? "Resume embeddings" : "Start embeddings"}
            </button>
          )}
        </div>
      </div>
      <div className="knowledge-import-job">
        <p className="knowledge-import-stats">
          Source: {choice ? sourceLabel(choice.source) : "Loading…"} · Model: {choice?.model || "None"}
        </p>
        <p className="knowledge-import-stats" role="status" aria-live="polite">
          Status: {job?.cancelRequested && active ? "Cancelling" :
            job ? job.status.charAt(0).toUpperCase() + job.status.slice(1) :
            choice?.source === "off" ? "Off" : "Not started"}
        </p>
        {job ? (
          <>
            <p className="knowledge-import-stats">
              {job.chunksEmbedded} embedded · {job.chunksRemaining} pending chunks (this run)
            </p>
            {job.status === "running" && (
              <progress
                className="knowledge-import-progress"
                aria-label="Knowledge embedding progress"
                max={Math.max(job.chunksTotal, 1)}
                value={job.chunksTotal > 0 ? Math.min(job.chunksEmbedded, job.chunksTotal) : undefined}
              />
            )}
          </>
        ) : (
          <p className="knowledge-import-stats">
            {choice?.source === "off"
              ? "Enable a source in Settings > Knowledge to generate embeddings."
              : "No embedding job for this workspace and model yet."}
          </p>
        )}
        {lastError && <p className="knowledge-watch-error" role="alert">Last error: {lastError}</p>}
        {actionError && <p className="knowledge-watch-error" role="alert">{actionError}</p>}
      </div>
    </section>
  );
}
