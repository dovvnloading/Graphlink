import { useCallback, useEffect, useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { Dialog, useOverlays } from "../overlays/overlays";

/**
 * ADR-017 stage 17.5: the "Knowledge" search panel - a human-driven,
 * request/reply front end for backend/api/intents_knowledge.py's own
 * `knowledge/search` intent (backend.knowledge_retrieval.hybrid_search()
 * underneath - lexical FTS5 always, fused with vector search whenever an
 * embedding provider/model is configured, degrading gracefully to
 * lexical-only otherwise). Distinct from backend/tools_knowledge.py's
 * ToolRegistry-registered `knowledge.search` tool - that one is for a
 * future ADR-008 model-driven tool call inside a live conversation; this
 * one is for a person searching their own ingested knowledge base
 * directly, mirroring DiagnosticsDialog's own transport.request()
 * "I need the actual return value" pattern (not fireIntent, which is
 * `: void`).
 *
 * "N sources used" (the ADR's own stage-17.5 exit criterion phrasing):
 * the result count heading below. "Opens the cited source at the right
 * offset": each result IS already the exact passage recorded at
 * `document[offsetStart:offsetEnd]` (backend.knowledge_chunking's own
 * offset-exactness contract) - expanding a card reveals that precise
 * span verbatim, and a source whose `sourceUri` is a real http(s) URL
 * (a web-research-retained page) gets a genuine "Open source" link. A
 * local file path has no OS-level "jump to this byte offset" mechanism
 * anywhere in this codebase (verified before building this - see this
 * ADR's own stage 17.5 recon) - this component does not fabricate one;
 * the offset is shown as citation metadata and the excerpt itself IS the
 * content at that offset, which is the honest, buildable version of the
 * exit criterion's own claim.
 */

interface KnowledgeSearchResult {
  chunkId: number;
  documentId: number;
  documentTitle: string;
  sourceUri: string;
  text: string;
  offsetStart: number;
  offsetEnd: number;
  rankSignals?: { fts5?: number; vector?: number };
}


interface KnowledgeLibraryDocument {
  documentId: number;
  title: string;
  type: string;
  sourceUri: string;
  sizeBytes: number | null;
  chunks: number;
  addedAt: string;
  status: "indexed";
  canReindex: boolean;
}

interface WatchedFolderSyncJob {
  filesTotal: number;
  filesProcessed: number;
  added: number;
  updated: number;
  removed: number;
  skipped: number;
  failedFiles: number;
  error: string;
}

interface WatchedFolder {
  id: number;
  path: string;
  fileCount: number;
  lastSyncAt: string | null;
  lastError: string;
  syncStatus: ImportStatus | "idle";
  lastJob: WatchedFolderSyncJob | null;
}

function isWatchSyncing(folder: WatchedFolder): boolean {
  return folder.syncStatus === "queued" || folder.syncStatus === "scanning" || folder.syncStatus === "running";
}

function formatLastSync(value: string | null): string {
  if (!value) return "Never";
  const time = Date.parse(value);
  return Number.isNaN(time) ? "—" : new Date(time).toLocaleString();
}

const DROP_CHUNK_BYTES = 192 * 1024;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_JOB_BYTES = 500 * 1024 * 1024;

function formatSize(value: number | null): string {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  return `${(value / (value < 1024 * 1024 ? 1024 : 1024 * 1024)).toFixed(1)} ${value < 1024 * 1024 ? "KiB" : "MiB"}`;
}

function formatAdded(value: string): string {
  const time = Date.parse(value);
  return Number.isNaN(time) ? "—" : new Date(time).toLocaleDateString();
}

function formatType(document: KnowledgeLibraryDocument): string {
  const extension = /\.([a-z0-9]+)$/i.exec(document.title)?.[1];
  return extension ? extension.toUpperCase() : document.type;
}

function encodeChunk(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let raw = "";
  for (let index = 0; index < bytes.length; index += 8192) {
    raw += String.fromCharCode(...bytes.subarray(index, index + 8192));
  }
  return btoa(raw);
}

type ImportStatus = "queued" | "scanning" | "running" | "completed" | "cancelled" | "failed";

interface KnowledgeImportJob {
  jobId: string;
  kind: "file" | "folder";
  workspaceId: number | null;
  status: ImportStatus;
  filesTotal: number;
  filesProcessed: number;
  bytesTotal: number;
  bytesProcessed: number;
  added: number;
  updated: number;
  duplicates: number;
  unchanged: number;
  skipped: number;
  failedFiles: number;
  lastFile: string;
  error: string;
  warnings: string[];
  cancelRequested: boolean;
}

function isActive(job: KnowledgeImportJob): boolean {
  return job.status === "queued" || job.status === "scanning" || job.status === "running";
}

function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function KnowledgeResultCard({ result }: { result: KnowledgeSearchResult }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <li className="knowledge-result-item">
      <button
        type="button"
        className="knowledge-result-header"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="knowledge-result-title">{result.documentTitle}</span>
        <span className="knowledge-result-source">{result.sourceUri}</span>
        {(typeof result.rankSignals?.fts5 === "number" || typeof result.rankSignals?.vector === "number") && (
          <span className="knowledge-result-signals" role="group" aria-label="Match signals">
            {typeof result.rankSignals?.fts5 === "number" && (
              <span className="knowledge-result-signal" title="FTS5 keyword match rank">
                Keyword #{result.rankSignals.fts5}
              </span>
            )}
            {typeof result.rankSignals?.vector === "number" && (
              <span className="knowledge-result-signal" title="Vector semantic match rank">
                Semantic #{result.rankSignals.vector}
              </span>
            )}
          </span>
        )}
      </button>
      {expanded && (
        <div className="knowledge-result-body">
          <p className="knowledge-result-excerpt">{result.text}</p>
          <div className="knowledge-result-footer">
            <span className="knowledge-result-offset">
              Offset {result.offsetStart}–{result.offsetEnd}
            </span>
            {isHttpUrl(result.sourceUri) && (
              <a
                className="knowledge-result-open-link"
                href={result.sourceUri}
                target="_blank"
                rel="noreferrer"
              >
                Open source
              </a>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export function KnowledgeSearchDialog({
  transport,
  onOpenDocumentView,
}: {
  transport: WsTransport;
  onOpenDocumentView?: (content: string, sourceLabel: string) => void;
}) {
  const overlays = useOverlays();
  const [documents, setDocuments] = useState<KnowledgeLibraryDocument[]>([]);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [documentAction, setDocumentAction] = useState<number | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<number | null>(null);
  const [watchedFolders, setWatchedFolders] = useState<WatchedFolder[]>([]);
  const [loadingWatches, setLoadingWatches] = useState(true);
  const [watchError, setWatchError] = useState<string | null>(null);
  const [watchAction, setWatchAction] = useState<string | null>(null);
  const [confirmRemoveWatch, setConfirmRemoveWatch] = useState<number | null>(null);
  const latestWatchRequest = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ sent: number; total: number } | null>(null);
  const uploadIdRef = useRef<string | null>(null);
  const abortUploadRef = useRef(false);
  const latestLibraryRequest = useRef(0);
  const completedJobsRef = useRef(new Set<string>());

  const refreshDocuments = useCallback(() => {
    const requestId = ++latestLibraryRequest.current;
    // Handle the response asynchronously so an effect that starts a refresh
    // never causes a synchronous state update during its execution.
    return transport.request("knowledge", "listDocuments", [])
      .then((value) => {
        const result = value as { documents: KnowledgeLibraryDocument[] } | null;
        if (requestId !== latestLibraryRequest.current) return;
        if (!result || !Array.isArray(result.documents)) throw new Error("Invalid document list response.");
        setDocuments(result.documents);
        setLibraryError(null);
      })
      .catch((cause: unknown) => {
        if (requestId === latestLibraryRequest.current) {
          setLibraryError(cause instanceof Error ? cause.message : "Could not load documents.");
        }
      })
      .finally(() => {
        if (requestId === latestLibraryRequest.current) setLoadingDocuments(false);
      });
  }, [transport]);

  const refreshWatchedFolders = useCallback(() => {
    const requestId = ++latestWatchRequest.current;
    return transport.request("knowledge", "listWatchedFolders", [])
      .then((value) => {
        if (requestId !== latestWatchRequest.current) return;
        const result = value as { folders: WatchedFolder[] } | null;
        if (!result || !Array.isArray(result.folders)) throw new Error("Invalid watched folder list response.");
        setWatchedFolders(result.folders);
        setWatchError(null);
      })
      .catch((cause: unknown) => {
        if (requestId === latestWatchRequest.current) {
          setWatchError(cause instanceof Error ? cause.message : "Could not load watched folders.");
        }
      })
      .finally(() => {
        if (requestId === latestWatchRequest.current) setLoadingWatches(false);
      });
  }, [transport]);

  const knowledgeOpen = overlays.isOpen("knowledge");
  useEffect(() => {
    if (knowledgeOpen) {
      void refreshDocuments();
      void refreshWatchedFolders();
    }
  }, [knowledgeOpen, refreshDocuments, refreshWatchedFolders]);

  // Watched syncs run in an app-lifetime service, not the dialog's
  // knowledge-ingest topic. Refresh while open so periodic and manual
  // syncs eventually display their persisted timestamps and file counts.
  const watchRefreshDelay = watchedFolders.some(isWatchSyncing) ? 1_000 : 10_000;
  useEffect(() => {
    if (!knowledgeOpen || watchedFolders.length === 0) return;
    const timer = window.setTimeout(() => { void refreshWatchedFolders(); }, watchRefreshDelay);
    return () => window.clearTimeout(timer);
  }, [knowledgeOpen, watchedFolders, watchRefreshDelay, refreshWatchedFolders]);

  async function watchedFolderIntent(
    action: "addWatchedFolder" | "removeWatchedFolder" | "syncWatchedFolders",
    folderId?: number,
  ) {
    if (watchAction !== null || (action === "addWatchedFolder" && picking !== null)) return;
    setWatchAction(`${action}:${folderId ?? "all"}`);
    setWatchError(null);
    try {
      const result = await transport.request(
        "knowledge", action, folderId === undefined ? [] : [folderId],
        action === "addWatchedFolder" ? 5 * 60_000 : undefined,
      ) as { cancelled?: boolean };
      if (action === "removeWatchedFolder") setConfirmRemoveWatch(null);
      if (!result?.cancelled) await refreshWatchedFolders();
    } catch (cause) {
      setWatchError(cause instanceof Error ? cause.message : "Watched folder action failed.");
    } finally {
      setWatchAction(null);
    }
  }

  async function documentIntent(documentId: number, action: "openDocument" | "removeDocument" | "reindexDocument") {
    if (documentAction !== null) return;
    setDocumentAction(documentId);
    setLibraryError(null);
    try {
      const result = await transport.request("knowledge", action, [documentId]) as {
        content?: string;
        title?: string;
      };
      if (action === "openDocument") {
        if (!onOpenDocumentView) throw new Error("Document View is unavailable.");
        onOpenDocumentView(result.content ?? "", result.title ?? "Knowledge document");
        overlays.close();
      } else if (action === "removeDocument") {
        setConfirmRemove(null);
        await refreshDocuments();
      }
    } catch (cause) {
      setLibraryError(cause instanceof Error ? cause.message : "Document action failed.");
    } finally {
      setDocumentAction(null);
    }
  }

  async function uploadFiles(files: File[]) {
    if (files.length === 0 || picking || uploadProgress || importJobs.some(isActive)) return;
    const total = files.reduce((sum, file) => sum + file.size, 0);
    if (files.length > 10_000 || total > MAX_JOB_BYTES || files.some((file) => file.size > MAX_FILE_BYTES)) {
      setImportError("Drop limit: 10,000 files, 25 MiB per file, 500 MiB per job.");
      return;
    }
    abortUploadRef.current = false;
    setImportError(null);
    setUploadProgress({ sent: 0, total });
    let completed = false;
    try {
      const begun = await transport.request("knowledge", "beginUpload", [
        files.map((file) => ({ name: file.name, size: file.size })),
      ]) as { uploadId: string };
      uploadIdRef.current = begun.uploadId;
      let sent = 0;
      for (const [fileIndex, file] of files.entries()) {
        for (let offset = 0; offset < file.size; offset += DROP_CHUNK_BYTES) {
          if (abortUploadRef.current) throw new Error("Upload cancelled.");
          const bytes = await file.slice(offset, offset + DROP_CHUNK_BYTES).arrayBuffer();
          await transport.request("knowledge", "appendUpload", [
            begun.uploadId, fileIndex, encodeChunk(bytes),
          ]);
          sent += bytes.byteLength;
          setUploadProgress({ sent, total });
        }
      }
      if (abortUploadRef.current) throw new Error("Upload cancelled.");
      await transport.request("knowledge", "finishUpload", [begun.uploadId]);
      completed = true;
    } catch (cause) {
      setImportError(cause instanceof Error ? cause.message : "Could not upload dropped files.");
    } finally {
      if (!completed && uploadIdRef.current) {
        void transport.request("knowledge", "cancelUpload", [uploadIdRef.current]).catch(() => {});
      }
      uploadIdRef.current = null;
      setUploadProgress(null);
    }
  }

  function cancelUpload() {
    abortUploadRef.current = true;
  }

  // Closing the dialog while a browser transfer is in progress discards
  // incomplete staged uploads. Finished ingestion jobs continue in the
  // existing backend manager and remain cancellable after reopening.
  useEffect(() => () => {
    abortUploadRef.current = true;
    if (uploadIdRef.current) {
      void transport.request("knowledge", "cancelUpload", [uploadIdRef.current]).catch(() => {});
    }
  }, [transport]);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [importJobs, setImportJobs] = useState<KnowledgeImportJob[]>([]);
  const [picking, setPicking] = useState<"files" | "folder" | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  // A topic snapshot survives dialog unmount/reopen and websocket reconnect;
  // no polling or artificial timeouts are needed for background work.
  useEffect(
    () =>
      transport.subscribe("knowledge-ingest", (payload) => {
        if (Array.isArray(payload.jobs)) {
          const jobs = payload.jobs as KnowledgeImportJob[];
          setImportJobs(jobs);
          for (const job of jobs) {
            if (!isActive(job) && !completedJobsRef.current.has(job.jobId)) {
              completedJobsRef.current.add(job.jobId);
              void refreshDocuments();
            }
          }
        }
      }),
    [transport, refreshDocuments],
  );

  function beginImport(kind: "files" | "folder") {
    if (picking || uploadProgress || importJobs.some(isActive) || watchAction === "addWatchedFolder:all") return;
    setPicking(kind);
    setImportError(null);
    // The picker can legitimately remain open for minutes. This request
    // resolves as soon as the selection starts an asynchronous job.
    void transport
      .request("knowledge", kind === "files" ? "addFiles" : "addFolder", [], 5 * 60_000)
      .catch((cause: unknown) => {
        setImportError(cause instanceof Error ? cause.message : "Could not start the import.");
      })
      .finally(() => setPicking(null));
  }

  function cancelImport(jobId: string) {
    void transport.request("knowledge", "cancelIngest", [jobId]).catch((cause: unknown) => {
      setImportError(cause instanceof Error ? cause.message : "Could not cancel the import.");
    });
  }

  // Adversarial-review finding: vector search embeds the query via a
  // network round-trip, so latency is not correlated with send order - a
  // second search fired while the first is still pending could otherwise
  // have its response arrive AFTER a later request's and silently
  // overwrite fresher results (or a slow failure could hide a fast
  // success). A monotonically increasing sequence token, checked before
  // ever touching state in a resolved/rejected handler, makes only the
  // MOST RECENTLY SENT request's own response ever win - matches the
  // "ignore stale in-flight responses" pattern this codebase already uses
  // elsewhere for the same reason (e.g. WsTransport's own per-request id
  // correlation).
  const latestRequestId = useRef(0);

  function runSearch() {
    const trimmed = query.trim();
    if (!trimmed || searching) return;
    const requestId = ++latestRequestId.current;
    setSearching(true);
    setError(null);
    transport
      .request("knowledge", "search", [trimmed, 10])
      .then((value) => {
        if (requestId !== latestRequestId.current) return; // a newer search has since been sent
        const payload = value as { results: KnowledgeSearchResult[] };
        setResults(payload.results);
      })
      .catch(() => {
        if (requestId !== latestRequestId.current) return;
        setError("Search failed - see graphlink.log for details.");
      })
      .finally(() => {
        if (requestId === latestRequestId.current) setSearching(false);
      });
  }

  return (
    <Dialog name="knowledge" title="Knowledge Library" className="knowledge-search-dialog">
      <p className="knowledge-library-intro">Documents indexed for the current workspace</p>
      <div className="knowledge-import-row">
        <button
          type="button"
          className="knowledge-search-button"
          disabled={picking !== null || uploadProgress !== null || importJobs.some(isActive) || watchAction === "addWatchedFolder:all"}
          onClick={() => beginImport("files")}
        >
          {picking === "files" ? "Choosing files…" : "Add files"}
        </button>
        <button
          type="button"
          className="knowledge-search-button"
          disabled={picking !== null || uploadProgress !== null || importJobs.some(isActive) || watchAction === "addWatchedFolder:all"}
          onClick={() => beginImport("folder")}
        >
          {picking === "folder" ? "Choosing folder…" : "Add folder"}
        </button>
        <span className="knowledge-import-hint">
          PDF, DOCX, XLSX, Markdown, text, CSV, JSON, HTML, code ·
          25 MiB/file; 500 MiB or 10,000 files/job
        </span>
      </div>
      <div
        className={"knowledge-drop-zone" + (dragging ? " knowledge-drop-active" : "")}
        role="region"
        aria-label="Drop files to index"
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void uploadFiles(Array.from(event.dataTransfer.files));
        }}
      >
        Drag and drop files here to index them in this workspace
      </div>
      {uploadProgress && (
        <div className="knowledge-import-job" aria-label="File upload progress">
          <div className="knowledge-import-head">
            <span>Uploading files · {formatSize(uploadProgress.sent)} / {formatSize(uploadProgress.total)}</span>
            <button type="button" className="knowledge-search-button" onClick={cancelUpload}>Cancel upload</button>
          </div>
          <progress aria-label="Bytes uploaded" className="knowledge-import-progress"
            max={Math.max(uploadProgress.total, 1)} value={uploadProgress.sent} />
        </div>
      )}
      {importError && <p className="knowledge-empty" role="alert">{importError}</p>}
      {importJobs.map((job) => (
        <div key={job.jobId} className="knowledge-import-job" aria-label="Knowledge import progress">
          <div className="knowledge-import-head">
            <span>
              {job.kind === "folder" ? "Folder" : "Files"} import: {job.status}
              {job.cancelRequested && isActive(job) ? " (cancelling…)" : ""}
            </span>
            {isActive(job) && (
              <button
                type="button"
                className="knowledge-search-button"
                onClick={() => cancelImport(job.jobId)}
                disabled={job.cancelRequested}
              >
                Cancel import
              </button>
            )}
          </div>
          <progress
            className="knowledge-import-progress"
            max={Math.max(job.filesTotal, 1)}
            value={job.filesProcessed}
            aria-label="Files processed"
          />
          <p className="knowledge-import-stats">
            {job.filesProcessed}/{job.filesTotal} files · {job.added} added · {job.updated} updated ·{" "}
            {job.duplicates + job.unchanged} deduplicated · {job.skipped} skipped ·{" "}
            {job.failedFiles} failed
          </p>
          {job.lastFile && <p className="knowledge-import-stats">Last: {job.lastFile}</p>}
          {job.error && <p className="knowledge-empty" role="alert">{job.error}</p>}
          {job.warnings.length > 0 && <p className="knowledge-import-stats">{job.warnings[0]}</p>}
        </div>
      ))}
      <section aria-label="Workspace documents" className="knowledge-library-section">
        <div className="knowledge-library-heading">
          <h2 className="knowledge-section-title">Documents ({documents.length})</h2>
          <button type="button" className="knowledge-search-button"
            onClick={() => void refreshDocuments()}>Refresh</button>
        </div>
        {libraryError && <p role="alert" className="knowledge-empty">{libraryError}</p>}
        {loadingDocuments ? <p role="status" className="knowledge-empty">Loading documents…</p> : documents.length === 0 ? (
          <p className="knowledge-empty">No documents yet. Add files or a folder to start indexing.</p>
        ) : (
          <div className="knowledge-library-table-scroll">
            <table className="knowledge-library-table">
              <thead><tr>
                <th scope="col">Title</th><th scope="col">Type</th><th scope="col">Size</th>
                <th scope="col">Chunks</th><th scope="col">Added</th>
                <th scope="col">Status</th><th scope="col">Actions</th>
              </tr></thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.documentId}>
                    <td className="knowledge-library-title" title={doc.sourceUri}>{doc.title}</td>
                    <td>{formatType(doc)}</td><td>{formatSize(doc.sizeBytes)}</td>
                    <td>{doc.chunks}</td><td>{formatAdded(doc.addedAt)}</td>
                    <td>{doc.status === "indexed" ? "Indexed" : doc.status}</td>
                    <td>
                      <div className="knowledge-library-actions">
                        <button type="button" className="knowledge-search-button"
                          disabled={documentAction !== null}
                          aria-label={`Open ${doc.title} in Document View`}
                          onClick={() => void documentIntent(doc.documentId, "openDocument")}>Open</button>
                        <button type="button" className="knowledge-search-button"
                          disabled={!doc.canReindex || documentAction !== null || importJobs.some(isActive) || uploadProgress !== null}
                          title={doc.canReindex ? "Re-read the indexed source files" : "No local file source available"}
                          aria-label={`Re-index ${doc.title}`}
                          onClick={() => void documentIntent(doc.documentId, "reindexDocument")}>Re-index</button>
                        {confirmRemove === doc.documentId ? (
                          <>
                            <button type="button" className="knowledge-search-button"
                              disabled={documentAction !== null || importJobs.some(isActive)}
                              onClick={() => void documentIntent(doc.documentId, "removeDocument")}>
                              Confirm remove
                            </button>
                            <button type="button" className="knowledge-search-button" onClick={() => setConfirmRemove(null)}>
                              Keep
                            </button>
                          </>
                        ) : (
                          <button type="button" className="knowledge-search-button"
                            disabled={documentAction !== null || importJobs.some(isActive) || uploadProgress !== null}
                            aria-label={`Remove ${doc.title}`}
                            onClick={() => setConfirmRemove(doc.documentId)}>Remove</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section aria-label="Watched folders" className="knowledge-library-section">
        <div className="knowledge-library-heading">
          <h2 className="knowledge-section-title">Watched folders ({watchedFolders.length})</h2>
          <div className="knowledge-library-actions">
            <button type="button" className="knowledge-search-button"
              disabled={watchAction !== null || picking !== null || uploadProgress !== null}
              onClick={() => void watchedFolderIntent("addWatchedFolder")}>
              {watchAction === "addWatchedFolder:all" ? "Choosing watched folder…" : "Add watched folder"}
            </button>
            <button type="button" className="knowledge-search-button"
              disabled={watchAction !== null || watchedFolders.length === 0 || watchedFolders.every(isWatchSyncing)}
              onClick={() => void watchedFolderIntent("syncWatchedFolders")}>Sync all now</button>
            <button type="button" className="knowledge-search-button"
              onClick={() => void refreshWatchedFolders()}>Refresh watched folders</button>
          </div>
        </div>
        <p className="knowledge-library-intro">
          Watched folders sync automatically about every 10 minutes. Removing a watch keeps indexed documents.
        </p>
        {watchError && <p role="alert" className="knowledge-watch-error">{watchError}</p>}
        {loadingWatches ? <p role="status" className="knowledge-empty">Loading watched folders…</p> :
          watchedFolders.length === 0 ? (
            <p className="knowledge-empty">No watched folders yet. Add one to keep its files indexed.</p>
          ) : (
            <div className="knowledge-library-table-scroll">
              <table className="knowledge-library-table">
                <thead><tr>
                  <th scope="col">Folder</th><th scope="col">Files</th>
                  <th scope="col">Last successful sync</th><th scope="col">Status</th>
                  <th scope="col">Actions</th>
                </tr></thead>
                <tbody>
                  {watchedFolders.map((folder) => (
                    <tr key={folder.id}>
                      <td className="knowledge-library-title knowledge-watch-path" title={folder.path}>
                        {folder.path}
                      </td>
                      <td>
                        {folder.fileCount} indexed
                        {folder.lastJob && (
                          <p className="knowledge-import-stats">
                            {folder.lastJob.filesProcessed}/{folder.lastJob.filesTotal} scanned ·{" "}
                            {folder.lastJob.added} added · {folder.lastJob.updated} updated ·{" "}
                            {folder.lastJob.removed} removed · {folder.lastJob.skipped} skipped ·{" "}
                            {folder.lastJob.failedFiles} failed
                          </p>
                        )}
                      </td>
                      <td>{folder.lastSyncAt ? (
                        <time dateTime={folder.lastSyncAt}>{formatLastSync(folder.lastSyncAt)}</time>
                      ) : "Never"}</td>
                      <td>
                        {folder.syncStatus === "idle" ? "Idle" :
                          folder.syncStatus.charAt(0).toUpperCase() + folder.syncStatus.slice(1)}
                        {(folder.lastError || (folder.syncStatus === "failed" && folder.lastJob?.error)) && (
                          <p role="alert" className="knowledge-watch-error">
                            Last error: {folder.lastError || folder.lastJob?.error}
                          </p>
                        )}
                      </td>
                      <td>
                        <div className="knowledge-library-actions">
                          <button type="button" className="knowledge-search-button"
                            aria-label={`Sync ${folder.path} now`}
                            disabled={watchAction !== null || isWatchSyncing(folder)}
                            onClick={() => void watchedFolderIntent("syncWatchedFolders", folder.id)}>
                            Sync now
                          </button>
                          {confirmRemoveWatch === folder.id ? (
                            <>
                              <button type="button" className="knowledge-search-button"
                                aria-label={`Confirm remove watched folder ${folder.path}`}
                                disabled={watchAction !== null}
                                onClick={() => void watchedFolderIntent("removeWatchedFolder", folder.id)}>
                                Confirm remove
                              </button>
                              <button type="button" className="knowledge-search-button"
                                aria-label={`Keep watching ${folder.path}`}
                                disabled={watchAction !== null}
                                onClick={() => setConfirmRemoveWatch(null)}>Keep</button>
                            </>
                          ) : (
                            <button type="button" className="knowledge-search-button"
                              aria-label={`Remove watched folder ${folder.path}`}
                              title="Stop syncing this folder without removing indexed documents"
                              disabled={watchAction !== null}
                              onClick={() => setConfirmRemoveWatch(folder.id)}>Remove</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </section>
      <h2 className="knowledge-section-title">Search indexed sources</h2>
      <div className="knowledge-search-row">
        <input
          type="text"
          className="knowledge-search-input"
          aria-label="Search the knowledge base"
          placeholder="Search your ingested knowledge base…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") runSearch();
          }}
        />
        <button
          type="button"
          className="knowledge-search-button"
          onClick={runSearch}
          disabled={searching || !query.trim()}
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {error && (
        <p className="knowledge-empty" role="alert">
          {error}
        </p>
      )}

      {results != null && !error && (
        <>
          <h2 className="knowledge-section-title">
            {results.length === 0 ? "No sources found" : `${results.length} source${results.length === 1 ? "" : "s"} used`}
          </h2>
          <ul className="knowledge-result-list">
            {results.map((result) => (
              <KnowledgeResultCard key={result.chunkId} result={result} />
            ))}
          </ul>
        </>
      )}
    </Dialog>
  );
}
