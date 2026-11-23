import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { Dialog, useOverlays } from "../overlays/overlays";
import type { KnowledgeCitationSource } from "../canvas/ChatCitationChips";
import { KnowledgeEmbeddingStatus } from "./KnowledgeEmbeddingStatus";
import { knowledgeSourceLabel } from "../knowledgeSourceLabel";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { MenuButton, MenuItem } from "../ui/Menu";
import { TextField } from "../ui/TextField";
import { Tabs } from "../ui/Tabs";
import { Badge, StatusPill } from "../ui/Badge";
import { ProgressBar } from "../ui/ProgressBar";
import { Spinner } from "../ui/Spinner";
import { Icon } from "../ui/icons/Icon";
import type { IconName } from "../ui/icons/registry";
import { useConfirm } from "../ui/ConfirmDialog";
import { formatBytes, formatCount } from "../ui/format";
import "./knowledge-library.css";

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
 * "N search results" labels the retrieved hits rather than implying that
 * the sources were used in a chat response. Server-derived citation identifiers
 * open the existing Document View through knowledge/openCitation, whose
 * workspace and chunk identity checks precede highlighting. The card
 * reveals the exact passage, and a genuine http(s) source also has its
 * external Open source link. No OS-level file-path jump is fabricated.
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
  citationId?: string;
  sourceFile?: string;
  section?: string;
}


interface KnowledgeLibraryDocument {
  documentId: number;
  title: string;
  type: string;
  sourceUri: string;
  sizeBytes: number | null;
  chunks: number;
  addedAt: string;
  status: "indexed" | "embedding" | "failed";
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

const DOCUMENT_PAGE_SIZE = 50;
const DROP_CHUNK_BYTES = 192 * 1024;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_JOB_BYTES = 500 * 1024 * 1024;

function formatSize(value: number | null): string {
  return value === null ? "—" : formatBytes(value);
}

function formatAdded(value: string): string {
  const time = Date.parse(value);
  return Number.isNaN(time) ? "—" : new Date(time).toLocaleDateString();
}

function formatType(document: KnowledgeLibraryDocument): string {
  if (document.type === "text/graph") return "Chat";
  if (document.type === "text/branch") return "Chat branch";
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

function knowledgeFileIcon(name: string, type = ""): IconName {
  const value = (name + " " + type).toLocaleLowerCase();
  if (/\.(ts|tsx|js|jsx|py|html|css|json|rs|go|sh|yml|yaml|xml)\b/.test(value)) return "file-code";
  if (/\.(csv|tsv|xls|xlsx|ods)\b/.test(value) || type.includes("spreadsheet")) return "file-table";
  if (/\.(png|jpe?g|gif|svg|webp|bmp)\b/.test(value) || type.includes("image")) return "file-image";
  return "document-text";
}

/** Render literal text, never search-result HTML, so highlighting cannot inject markup. */
function highlightedSnippet(text: string, term: string): ReactNode {
  const query = term.trim().toLocaleLowerCase();
  if (!query) return text;
  const parts: ReactNode[] = [];
  const lower = text.toLocaleLowerCase();
  let from = 0;
  let index = lower.indexOf(query, from);
  while (index >= 0) {
    if (index > from) parts.push(text.slice(from, index));
    parts.push(<mark key={index}>{text.slice(index, index + query.length)}</mark>);
    from = index + query.length;
    index = lower.indexOf(query, from);
  }
  if (from < text.length) parts.push(text.slice(from));
  return parts;
}

function KnowledgeResultCard({
  result, query, onOpenCitation, onOpenDocument,
}: {
  result: KnowledgeSearchResult;
  query: string;
  onOpenCitation?: (source: KnowledgeCitationSource) => void;
  onOpenDocument: () => void;
}) {
  const sourceLabel = knowledgeSourceLabel(result.sourceFile ?? result.sourceUri, result.documentTitle);
  const canOpenCitation = Boolean(onOpenCitation && /^kb:[0-9a-f]{20}$/.test(result.citationId ?? "") &&
    Number.isSafeInteger(result.documentId) && Number.isSafeInteger(result.chunkId) &&
    result.offsetStart >= 0 && result.offsetEnd >= result.offsetStart);
  function open() {
    if (canOpenCitation) {
      onOpenCitation?.({
        citationId: result.citationId ?? "", documentId: result.documentId,
        chunkId: result.chunkId, documentTitle: result.documentTitle,
        sourceUri: result.sourceUri, sourceFile: result.sourceFile ?? result.sourceUri,
        section: result.section ?? "Unsectioned", offsetStart: result.offsetStart,
        offsetEnd: result.offsetEnd, excerpt: result.text, cited: true,
      });
    } else onOpenDocument();
  }
  return (
    <li className="knowledge-result-item">
      <div className="knowledge-result-heading">
        <Icon name={knowledgeFileIcon(result.documentTitle)} size="md" />
        <span className="knowledge-result-title">{result.documentTitle}</span>
        {sourceLabel !== result.documentTitle && <span className="knowledge-result-source">{sourceLabel}</span>}
      </div>
      <p className="knowledge-result-excerpt">{highlightedSnippet(result.text, query)}</p>
      <div className="knowledge-result-footer">
        <Button size="sm" iconStart="document-text" onClick={open}>Open in Document View</Button>
        {isHttpUrl(result.sourceUri) && (
          <a className="knowledge-result-open-link" href={result.sourceUri} target="_blank" rel="noreferrer">
            Open source
          </a>
        )}
        {(typeof result.rankSignals?.fts5 === "number" || typeof result.rankSignals?.vector === "number") && (
          <span className="knowledge-result-signals" role="group" aria-label="Match signals">
            {typeof result.rankSignals?.fts5 === "number" && <Badge>Keyword #{result.rankSignals.fts5}</Badge>}
            {typeof result.rankSignals?.vector === "number" && <Badge>Semantic #{result.rankSignals.vector}</Badge>}
          </span>
        )}
      </div>
    </li>
  );
}

export function KnowledgeSearchDialog({
  transport,
  onOpenDocumentView,
}: {
  transport: WsTransport;
  onOpenDocumentView?: (content: string, sourceLabel: string, citation?: KnowledgeCitationSource) => void;
}) {
  const overlays = useOverlays();
  const askConfirm = useConfirm();
  const [documents, setDocuments] = useState<KnowledgeLibraryDocument[]>([]);
  const [documentPage, setDocumentPage] = useState(0);
  const documentPageCount = Math.max(1, Math.ceil(documents.length / DOCUMENT_PAGE_SIZE));
  const currentDocumentPage = Math.min(documentPage, documentPageCount - 1);
  const firstDocument = currentDocumentPage * DOCUMENT_PAGE_SIZE;
  const visibleDocuments = documents.slice(firstDocument, firstDocument + DOCUMENT_PAGE_SIZE);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [documentAction, setDocumentAction] = useState<number | null>(null);
  const [documentStatus, setDocumentStatus] = useState<string | null>(null);
  const [watchedFolders, setWatchedFolders] = useState<WatchedFolder[]>([]);
  const [loadingWatches, setLoadingWatches] = useState(true);
  const [watchError, setWatchError] = useState<string | null>(null);
  const [watchAction, setWatchAction] = useState<string | null>(null);
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

  // Branch opt-ins/out and external source removals change the Library without
  // an ingest job. Refresh while open when the backend publishes that change.
  useEffect(() => {
    if (!knowledgeOpen) return;
    return transport.subscribe("knowledge-library", () => { void refreshDocuments(); });
  }, [knowledgeOpen, transport, refreshDocuments]);

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
    setDocumentStatus(null);
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
        await refreshDocuments();
        setDocumentStatus("Document removed from this workspace.");
      } else {
        setDocumentStatus("Re-indexing queued.");
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

  const runSearch = useCallback((term: string) => {
    const trimmed = term.trim();
    const requestId = ++latestRequestId.current;
    if (!trimmed) {
      setResults(null);
      setError(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    setResults(null);
    setError(null);
    transport.request("knowledge", "search", [trimmed, 10])
      .then((value) => {
        if (requestId !== latestRequestId.current) return;
        const payload = value as { results: KnowledgeSearchResult[] };
        setResults(Array.isArray(payload?.results) ? payload.results : []);
      })
      .catch((cause: unknown) => {
        if (requestId !== latestRequestId.current) return;
        console.error("[knowledge] Search failed:", cause);
        setError("Search failed. See graphlink.log for details.");
      })
      .finally(() => {
        if (requestId === latestRequestId.current) setSearching(false);
      });
  }, [transport]);

  // A short debounce also works for semantic searches whose response can
  // arrive after the user has already changed the query.
  useEffect(() => {
    if (!knowledgeOpen) return;
    const timeout = window.setTimeout(() => runSearch(query), 250);
    return () => window.clearTimeout(timeout);
  }, [knowledgeOpen, query, runSearch]);

  async function removeDocument(doc: KnowledgeLibraryDocument) {
    const accepted = await askConfirm({
      title: "Remove document?",
      body: "Remove “" + doc.title + "” from the current workspace?",
      confirmLabel: "Remove document",
      destructive: true,
    });
    if (accepted) await documentIntent(doc.documentId, "removeDocument");
  }

  async function removeWatchedFolder(folder: WatchedFolder) {
    const accepted = await askConfirm({
      title: "Stop watching folder?",
      body: "Stop syncing “" + knowledgeSourceLabel(folder.path, folder.path) + "”? Indexed documents will be kept.",
      confirmLabel: "Remove watched folder",
      destructive: true,
    });
    if (accepted) await watchedFolderIntent("removeWatchedFolder", folder.id);
  }

  const importBusy = picking !== null || uploadProgress !== null || importJobs.some(isActive);
  const documentActionsBusy = documentAction !== null || importJobs.some(isActive) || uploadProgress !== null;

  return (
    <Dialog {...{ title: "Knowledge library" }} name="knowledge" size="xl" className="knowledge-search-dialog">
      <section className="knowledge-search-heading" aria-label="Search indexed sources">
        <div className="knowledge-search-row">
          <TextField
            type="search" label="Search the knowledge base" prefixIcon="search" suffix={{ type: "clear" }}
            placeholder="Search your ingested knowledge base…"
            value={query}
            onValueChange={(value) => {
              ++latestRequestId.current;
              setQuery(value);
              setResults(null);
              setSearching(false);
              setError(null);
            }}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); runSearch(query); } }}
            autoComplete="off"
          />
          <Button iconStart="search" onClick={() => runSearch(query)} disabled={!query.trim()}
            loading={searching}>Search</Button>
        </div>
        {searching && <p role="status" className="knowledge-library-muted"><Spinner size={14} decorative /> Searching indexed sources…</p>}
        {error && <p role="alert" className="knowledge-library-error">{error}</p>}
        {results !== null && !error && (
          <div className="knowledge-search-results-wrap">
            <p role="status" aria-live="polite" className="knowledge-library-status">
              {results.length === 0 ? "No search results" : formatCount(results.length) + " search result" + (results.length === 1 ? "" : "s")}
            </p>
            {results.length > 0 && (
              <ul className="knowledge-search-results">
                {results.map((result) => (
                  <KnowledgeResultCard key={result.chunkId} result={result} query={query}
                    onOpenCitation={onOpenDocumentView ? (source) => {
                      onOpenDocumentView("", source.documentTitle, source);
                      overlays.close();
                    } : undefined}
                    onOpenDocument={() => { void documentIntent(result.documentId, "openDocument"); }} />
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
      <Tabs label="Knowledge library sections" defaultValue="documents" items={[
        {
          id: "documents", label: "Documents (" + documents.length + ")",
          content: (
            <section className="knowledge-library-panel" aria-label="Workspace documents">
              <div className="knowledge-library-toolbar">
                <Button variant="primary" iconStart="plus"
                  disabled={importBusy || watchAction === "addWatchedFolder:all"}
                  loading={picking === "files"} onClick={() => beginImport("files")}>Add files</Button>
                <Button iconStart="folder" disabled={importBusy || watchAction === "addWatchedFolder:all"}
                  loading={picking === "folder"} onClick={() => beginImport("folder")}>Add folder</Button>
                <Button iconStart="refresh" size="sm" onClick={() => void refreshDocuments()}>Refresh</Button>
              </div>
              <p className="knowledge-import-hint">
                PDF, DOCX, XLSX, Markdown, text, CSV, JSON, HTML and code · 25 MB per file, 500 MB per job
              </p>
              <div className={"knowledge-drop-zone" + (dragging ? " knowledge-drop-zone-active" : "")}
                role="region" aria-label="Drop files to index"
                onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  void uploadFiles(Array.from(event.dataTransfer.files));
                }}>
                {dragging ? (
                  <div className="knowledge-drop-overlay">
                    <div className="knowledge-drop-overlay-content"><Icon name="upload" /> Drop files to index</div>
                  </div>
                ) : documents.length === 0 && !loadingDocuments ? (
                  <EmptyState icon="upload" {...{ title: "No documents yet" }}
                    body="Drag files here or choose Add files to index sources in this workspace." />
                ) : (
                  <div className="knowledge-drop-placeholder">Drop files here to add them to this workspace.</div>
                )}
              </div>
              {uploadProgress && (
                <div className="knowledge-import-job" aria-label="File upload progress">
                  <div className="knowledge-import-head">
                    <span>Uploading files · {formatSize(uploadProgress.sent)} / {formatSize(uploadProgress.total)}</span>
                    <Button size="sm" onClick={cancelUpload}>Cancel upload</Button>
                  </div>
                  <ProgressBar label="Bytes uploaded" max={Math.max(uploadProgress.total, 1)} value={uploadProgress.sent} />
                </div>
              )}
              {importError && <p role="alert" className="knowledge-library-error">{importError}</p>}
              {importJobs.map((job) => (
                <div key={job.jobId} className="knowledge-import-job" aria-label="Knowledge import progress">
                  <div className="knowledge-import-head">
                    <span>{job.kind === "folder" ? "Folder" : "Files"} import: {job.status}{job.cancelRequested && isActive(job) ? " (cancelling…)" : ""}</span>
                    {isActive(job) && <Button size="sm" disabled={job.cancelRequested}
                      onClick={() => cancelImport(job.jobId)}>Cancel import</Button>}
                  </div>
                  <ProgressBar label="Files processed" max={Math.max(job.filesTotal, 1)} value={job.filesProcessed} />
                  <p className="knowledge-import-stats">
                    {job.filesProcessed}/{job.filesTotal} files · {job.added} added · {job.updated} updated ·
                    {" "}{job.duplicates + job.unchanged} deduplicated · {job.skipped} skipped · {job.failedFiles} failed
                  </p>
                  {job.lastFile && <p className="knowledge-import-stats">Last: {knowledgeSourceLabel(job.lastFile)}</p>}
                  {job.error && <p role="alert" className="knowledge-library-error">{job.error}</p>}
                  {job.warnings.length > 0 && <p className="knowledge-import-stats">{job.warnings[0]}</p>}
                </div>
              ))}
              {libraryError && <p role="alert" className="knowledge-library-error">{libraryError}</p>}
              {documentStatus && <p role="status" className="knowledge-library-status">{documentStatus}</p>}
              {loadingDocuments ? <p role="status" className="knowledge-library-muted">Loading documents…</p> :
                documents.length > 0 && (
                <>
                  <ul className="knowledge-library-list">
                    {visibleDocuments.map((doc) => (
                      <li key={doc.documentId} className="knowledge-library-row">
                        <Icon name={knowledgeFileIcon(doc.title, doc.type)} size="lg" />
                        <span className="knowledge-library-copy">
                          <span className="knowledge-library-title">{doc.title}</span>
                          <span className="knowledge-library-subtitle">
                            {formatType(doc)} · {formatSize(doc.sizeBytes)} · {formatCount(doc.chunks)} chunks · Added {formatAdded(doc.addedAt)}
                          </span>
                        </span>
                        <span className="knowledge-library-row-actions">
                          <StatusPill status={doc.status === "indexed" ? "Done" : doc.status === "embedding" ? "Running" : "Failed"} />
                          <span className="knowledge-library-status">
                            {doc.status === "indexed" ? "Indexed" : doc.status === "embedding" ? "Embedding" : "Failed"}
                          </span>
                          <MenuButton label="Actions" menuLabel={"Actions for " + doc.title}
                            disabled={documentAction !== null}>
                            <MenuItem icon="document-text" label="Open in Document View"
                              onSelect={() => void documentIntent(doc.documentId, "openDocument")} />
                            <MenuItem icon="refresh" label="Re-index"
                              disabled={!doc.canReindex || documentActionsBusy}
                              onSelect={() => void documentIntent(doc.documentId, "reindexDocument")} />
                            <MenuItem label="Remove…" destructive disabled={documentActionsBusy}
                              onSelect={() => { void removeDocument(doc); }} />
                          </MenuButton>
                        </span>
                      </li>
                    ))}
                  </ul>
                  {documents.length > DOCUMENT_PAGE_SIZE && (
                    <nav aria-label="Document pages" className="knowledge-library-pages">
                      <Button size="sm" disabled={currentDocumentPage === 0}
                        onClick={() => setDocumentPage(currentDocumentPage - 1)}>Previous documents</Button>
                      <span role="status" className="knowledge-library-muted">
                        Showing {firstDocument + 1}–{Math.min(firstDocument + DOCUMENT_PAGE_SIZE, documents.length)} of {documents.length}
                        {" "}· Page {currentDocumentPage + 1} of {documentPageCount}
                      </span>
                      <Button size="sm" disabled={currentDocumentPage >= documentPageCount - 1}
                        onClick={() => setDocumentPage(currentDocumentPage + 1)}>Next documents</Button>
                    </nav>
                  )}
                </>
              )}
            </section>
          ),
        },
        {
          id: "watches", label: "Watched folders (" + watchedFolders.length + ")",
          content: (
            <section className="knowledge-library-panel" aria-label="Watched folders">
              <div className="knowledge-library-toolbar">
                <Button variant="primary" iconStart="folder" disabled={watchAction !== null || importBusy}
                  onClick={() => void watchedFolderIntent("addWatchedFolder")}>Add watched folder</Button>
                <Button iconStart="refresh"
                  disabled={watchAction !== null || watchedFolders.length === 0 || watchedFolders.every(isWatchSyncing)}
                  onClick={() => void watchedFolderIntent("syncWatchedFolders")}>Sync all now</Button>
                <Button size="sm" onClick={() => void refreshWatchedFolders()}>Refresh watched folders</Button>
              </div>
              <p className="knowledge-library-muted">
                Watched folders sync automatically about every 10 minutes. Removing a watch keeps indexed documents.
              </p>
              {watchError && <p role="alert" className="knowledge-library-error">{watchError}</p>}
              {loadingWatches ? <p role="status" className="knowledge-library-muted">Loading watched folders…</p> :
                watchedFolders.length === 0 ? (
                  <EmptyState icon="folder" {...{ title: "No watched folders yet" }}
                    body="Add a watched folder to keep local files indexed automatically." />
                ) : (
                  <ul className="knowledge-library-list">
                    {watchedFolders.map((folder) => (
                      <li key={folder.id} className="knowledge-library-row">
                        <Icon name="folder" size="lg" />
                        <span className="knowledge-library-copy">
                          <span className="knowledge-library-title">{folder.path}</span>
                          <span className="knowledge-library-subtitle">
                            {formatCount(folder.fileCount)} indexed · Last sync: {formatLastSync(folder.lastSyncAt)}
                          </span>
                          {folder.lastJob && <span className="knowledge-import-stats">
                            {folder.lastJob.filesProcessed}/{folder.lastJob.filesTotal} scanned · {folder.lastJob.added} added ·
                            {" "}{folder.lastJob.updated} updated · {folder.lastJob.removed} removed · {folder.lastJob.failedFiles} failed
                          </span>}
                          {(folder.lastError || folder.lastJob?.error) &&
                            <span role="alert" className="knowledge-library-error">
                              {folder.lastError || folder.lastJob?.error}
                            </span>}
                        </span>
                        <span className="knowledge-library-row-actions">
                          <StatusPill status={isWatchSyncing(folder) ? "Running" : folder.syncStatus === "failed" ? "Failed" : "Done"} />
                          <MenuButton label="Actions" menuLabel={"Actions for " + folder.path} disabled={watchAction !== null}>
                            <MenuItem label="Sync now" icon="refresh" disabled={isWatchSyncing(folder)}
                              onSelect={() => { void watchedFolderIntent("syncWatchedFolders", folder.id); }} />
                            <MenuItem label="Remove watched folder…" destructive
                              onSelect={() => { void removeWatchedFolder(folder); }} />
                          </MenuButton>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
            </section>
          ),
        },
        {
          id: "embeddings", label: "Embeddings",
          content: <KnowledgeEmbeddingStatus transport={transport} open={knowledgeOpen}
            onSetUpEmbeddings={() => {
              transport.fireIntent("app-settings", "setActiveSection", ["knowledge"], undefined, true);
              overlays.open("settings", "dialog");
            }} />,
        },
      ]} />
    </Dialog>
  );
}
