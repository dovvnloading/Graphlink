import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "../ui/icons/Icon";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { Tooltip } from "../ui/Tooltip";
import { Spinner } from "../ui/Spinner";
import type { HarnessWorkspaceListing } from "./harnessWorkspaceFiles";
import type { StateListener } from "../../lib/ws/transport";
import { KnowledgeFileImportStatus, useKnowledgeFileImport } from "../knowledgeFileImport";

/** On-demand folder browser. Only relative paths are sent; the backend
 * derives the root from the current node grant on every request. */
export function HarnessWorkspaceFiles({
  onList,
  onOpen,
  onAddToKnowledge,
  onSubscribeKnowledgeIngest,
  onReveal,
  onTrustChanged,
}: {
  onList: (path: string, cursor?: string) => Promise<HarnessWorkspaceListing>;
  onOpen: (path: string, expectedRoot: string) => Promise<void>;
  onAddToKnowledge?: (path: string, expectedRoot: string) => Promise<unknown>;
  onSubscribeKnowledgeIngest?: (listener: StateListener) => () => void;
  onReveal: () => void;
  onTrustChanged?: (listener: () => void) => () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [path, setPath] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [previousCursors, setPreviousCursors] = useState<(string | null)[]>([]);
  const requestRef = useRef(0);
  const openRequestRef = useRef(0);
  const [listing, setListing] = useState<HarnessWorkspaceListing | null>(null);
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState("");
  const subscribeKnowledge = useCallback(
    (listener: StateListener) => onSubscribeKnowledgeIngest?.(listener) ?? (() => {}),
    [onSubscribeKnowledgeIngest],
  );
  const knowledgeImport = useKnowledgeFileImport(subscribeKnowledge);

  // Fetch on user actions, not in an effect: this avoids an extra mount
  // request and keeps one pending directory navigation from replacing a
  // newer result if its response happens to arrive last.
  const loadFolder = useCallback((nextPath: string, nextCursor: string | null = null,
    previous: (string | null)[] = []) => {
    const request = ++requestRef.current;
    setPath(nextPath);
    setCursor(nextCursor);
    setPreviousCursors(previous);
    setLoading(true);
    setError("");
    setListing(null);
    void (nextCursor === null ? onList(nextPath) : onList(nextPath, nextCursor))
      .then((result) => {
        if (request === requestRef.current) setListing(result);
      })
      .catch((reason: unknown) => {
        if (request === requestRef.current) {
          setError(reason instanceof Error ? reason.message : "Could not list workspace files.");
        }
      })
      .finally(() => {
        if (request === requestRef.current) setLoading(false);
      });
  }, [onList]);

  function toggle() {
    if (expanded) {
      requestRef.current++;
      setExpanded(false);
      return;
    }
    setExpanded(true);
    loadFolder(path);
  }

  // A Settings grant/revocation can change the current root without
  // changing this node's harnessWorkspacePath (and therefore its React key).
  // Drop the old root and its clickable entries immediately; any pending
  // response from before the trust change is ignored by its generation.
  useEffect(() => {
    if (!onTrustChanged) return;
    return onTrustChanged(() => {
      requestRef.current++;
      openRequestRef.current++;
      setPath("");
      setCursor(null);
      setPreviousCursors([]);
      setListing(null);
      setLoading(false);
      setOpening(null);
      setError("");
      if (expanded) loadFolder("");
    });
  }, [onTrustChanged, expanded, loadFolder]);

  useEffect(() => () => {
    requestRef.current++;
    openRequestRef.current++;
  }, []);

  async function openFile(filePath: string) {
    if (!listing) return;
    const request = ++openRequestRef.current;
    setError("");
    setOpening(filePath);
    try {
      await onOpen(filePath, listing.root);
    } catch (reason: unknown) {
      if (request === openRequestRef.current) {
        setError(reason instanceof Error ? reason.message : "Could not open this file.");
      }
    } finally {
      if (request === openRequestRef.current) setOpening(null);
    }
  }

  const parent = path.split("/").slice(0, -1).join("/");
  return (
    <div className="harness-workspace-files">
      <div className="harness-workspace-files-actions">
        <Button size="sm" className="nodrag"
          aria-expanded={expanded}
          onClick={toggle}
        >
          {expanded ? "Hide files" : "Browse files"}
        </Button>
        <Button size="sm" variant="ghost" className="nodrag" onClick={onReveal}>Reveal in file manager</Button>
      </div>
      {expanded && (
        <section className="harness-files-browser nodrag nowheel" aria-label="Workspace files" aria-busy={loading}>
          <div className="harness-files-toolbar">
            <IconButton size="sm" className="nodrag" icon="arrow-up" label="Up one folder"
              disabled={!path || loading} onClick={() => loadFolder(parent)} />
            <Tooltip label={path || "/"}><span className="harness-files-current">{path || "/"}</span></Tooltip>
            <IconButton size="sm" className="nodrag" icon="refresh" label="Refresh workspace files"
              onClick={() => loadFolder(path)} />
          </div>
          {listing && (
            <div className="harness-files-root">
              {listing.isScratch ? "Scratch" : "Bound folder"}: {listing.root}
            </div>
          )}
          {loading && <p className="harness-files-note" role="status">Loading files…</p>}
          {error && <p className="harness-files-error" role="alert">{error}</p>}
          <KnowledgeFileImportStatus state={knowledgeImport} />
          {listing && listing.entries.length === 0 && (
            <p className="harness-files-note">No files in this folder.</p>
          )}
          {listing && listing.entries.length > 0 && (
            <ul className="harness-files-list">
              {listing.entries.map((entry) => (
                <li key={entry.path} className="harness-files-entry">
                  <button
                    type="button"
                    className="harness-files-entry-button nodrag"
                    aria-label={entry.isDir ? `Open folder ${entry.name}` : `Open ${entry.name} in Document View`}
                    disabled={opening !== null}
                    onClick={() => {
                      if (entry.isDir) loadFolder(entry.path);
                      else void openFile(entry.path);
                    }}
                  >
                    <Icon name={entry.isDir ? "folder" : "file"} size="sm" />
                    <span className="harness-files-name">{entry.name}</span>
                    {opening === entry.path && <Spinner size={12} decorative />}
                  </button>
                  {!entry.isDir && onAddToKnowledge && (
                    <Button size="sm" variant="ghost"
                      className="harness-files-add-knowledge nodrag"
                      aria-label={`Add ${entry.name} to knowledge`}
                      disabled={knowledgeImport.busy}
                      onClick={() => void knowledgeImport.add(
                        entry.name, () => onAddToKnowledge(entry.path, listing.root),
                      )}
                    >
                      Add to knowledge
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {listing && (previousCursors.length > 0 || listing.nextCursor) && (
            <div className="harness-files-pagination">
              <Button size="sm" className="nodrag"
                disabled={loading || previousCursors.length === 0}
                onClick={() => loadFolder(path, previousCursors[previousCursors.length - 1] ?? null,
                  previousCursors.slice(0, -1))}>
                Previous page
              </Button>
              <span>Page {previousCursors.length + 1}</span>
              <Button size="sm" className="nodrag"
                disabled={loading || !listing.nextCursor}
                onClick={() => loadFolder(path, listing.nextCursor ?? null, [...previousCursors, cursor])}>
                Next page
              </Button>
            </div>
          )}
          {listing?.nextCursor && (
            <p className="harness-files-note">Showing up to 300 entries per page. Use Next page to see more files.</p>
          )}
        </section>
      )}
    </div>
  );
}
