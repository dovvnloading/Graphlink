import { useEffect, useState } from "react";
import type { StateListener } from "../lib/ws/transport";
import { ProgressBar } from "./ui/ProgressBar";
import "./chrome/knowledge-library.css";

/** File imports run in PR #889's session-scoped background manager. The topic
 * survives reconnects and carries terminal results as well as progress. */
interface KnowledgeFileJob {
  jobId: string;
  status: "queued" | "scanning" | "running" | "completed" | "cancelled" | "failed";
  filesTotal: number;
  filesProcessed: number;
  added: number;
  updated: number;
  duplicates: number;
  unchanged: number;
  skipped: number;
  failedFiles: number;
  error: string;
  warnings: string[];
}

function isJob(value: unknown): value is KnowledgeFileJob {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<KnowledgeFileJob>;
  return typeof row.jobId === "string" && typeof row.status === "string" &&
    typeof row.filesTotal === "number" && typeof row.filesProcessed === "number" &&
    typeof row.added === "number" && typeof row.updated === "number" &&
    typeof row.duplicates === "number" && typeof row.unchanged === "number" &&
    typeof row.skipped === "number" && typeof row.failedFiles === "number" &&
    typeof row.error === "string" && Array.isArray(row.warnings);
}

function active(job: KnowledgeFileJob): boolean {
  return job.status === "queued" || job.status === "scanning" || job.status === "running";
}

export function useKnowledgeFileImport(subscribe: (listener: StateListener) => () => void) {
  const [jobs, setJobs] = useState<KnowledgeFileJob[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => subscribe((payload) => {
    if (Array.isArray(payload.jobs)) setJobs(payload.jobs.filter(isJob));
  }), [subscribe]);

  const job = jobs.find((candidate) => candidate.jobId === jobId) ?? null;
  // The manager only admits one active job per session, including imports
  // started from the Knowledge dialog or the other file surface.
  const busy = starting || (jobId !== null && job === null) || jobs.some(active);

  async function add(fileName: string, start: () => Promise<unknown>): Promise<void> {
    if (busy) return;
    setName(fileName);
    setJobId(null);
    setError("");
    setStarting(true);
    try {
      const result = await start();
      const id = (result as { jobId?: unknown } | null)?.jobId;
      if (typeof id !== "string" || !id) throw new Error("The import did not return a job ID.");
      setJobId(id);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Could not start the import.");
    } finally {
      setStarting(false);
    }
  }

  return { add, busy, starting, job, name, error, awaitingJob: jobId !== null && job === null };
}

type ImportState = ReturnType<typeof useKnowledgeFileImport>;

export function KnowledgeFileImportStatus({ state }: { state: ImportState }) {
  const { job, name, error, starting, awaitingJob } = state;
  if (!starting && !awaitingJob && !job && !error) return null;

  if (error) return <p role="alert" className="knowledge-file-import-message">
    Could not add {name} to knowledge: {error}
  </p>;

  if (starting || awaitingJob || (job && active(job))) {
    return (
      <div role="status" className="knowledge-file-import-message">
        <span>
          {starting || awaitingJob || job?.status === "queued" ? "Starting" :
            job?.status === "scanning" ? "Scanning" : "Adding"} {name} to knowledge…
          {job && job.filesTotal > 0 ? ` ${job.filesProcessed}/${job.filesTotal} files processed.` : ""}
        </span>
        {job && job.status === "running" && (
          <ProgressBar label="Knowledge import progress" max={Math.max(job.filesTotal, 1)} value={job.filesProcessed} />
        )}
      </div>
    );
  }

  if (!job) return null;
  const succeeded = job.added + job.updated + job.duplicates + job.unchanged;
  const warning = job.warnings[0];
  const problem = job.status === "failed" || job.failedFiles > 0 || job.skipped > 0;
  let message: string;
  if (job.status === "cancelled") {
    message = `Cancelled adding ${name} to knowledge.`;
  } else if (job.status === "failed") {
    message = `Could not add ${name} to knowledge: ${job.error || warning || "Import failed."}`;
  } else if (problem) {
    message = succeeded > 0
      ? `${name}: ${succeeded} indexed, ${job.failedFiles} failed, ${job.skipped} skipped. ${warning || ""}`
      : `Could not add ${name} to knowledge: ${warning || job.error || "File was skipped or could not be read."}`;
  } else if (job.added > 0) {
    message = `Added ${name} to this workspace's knowledge.`;
  } else if (job.updated > 0) {
    message = `Updated ${name} in this workspace's knowledge.`;
  } else if (succeeded > 0) {
    message = `${name} is already in this workspace's knowledge.`;
  } else {
    message = `No indexable content was added from ${name}.`;
  }
  return <p role={problem ? "alert" : "status"} className="knowledge-file-import-message">{message}</p>;
}
