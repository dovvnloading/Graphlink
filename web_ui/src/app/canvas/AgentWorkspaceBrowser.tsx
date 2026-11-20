import { useCallback, useEffect, useRef, useState } from "react";
import type { HarnessWorkspaceListing } from "./harnessWorkspaceFiles";

/** On-demand folder browser. Only relative paths are sent; the backend
 * derives the root from the current node grant on every request. */
export function HarnessWorkspaceFiles({
  onList,
  onOpen,
  onReveal,
  onTrustChanged,
}: {
  onList: (path: string, cursor?: string) => Promise<HarnessWorkspaceListing>;
  onOpen: (path: string, expectedRoot: string) => Promise<void>;
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
        <button
          type="button"
          className="plan-node-button nodrag"
          aria-expanded={expanded}
          onClick={toggle}
        >
          {expanded ? "Hide files" : "Browse files"}
        </button>
        <button type="button" className="plan-node-button nodrag" onClick={onReveal}>
          Reveal in file manager
        </button>
      </div>
      {expanded && (
        <section className="harness-files-browser nodrag nowheel" aria-label="Workspace files" aria-busy={loading}>
          <div className="harness-files-toolbar">
            <button type="button" className="plan-node-button nodrag" disabled={!path || loading}
              onClick={() => loadFolder(parent)} aria-label="Up one folder">
              Up
            </button>
            <span className="harness-files-current" title={path || "/"}>{path || "/"}</span>
            <button type="button" className="plan-node-button nodrag"
              onClick={() => loadFolder(path)} aria-label="Refresh workspace files">
              Refresh
            </button>
          </div>
          {listing && (
            <div className="harness-files-root" title={listing.root}>
              {listing.isScratch ? "Scratch" : "Bound folder"}: {listing.root}
            </div>
          )}
          {loading && <p className="harness-files-note" role="status">Loading files…</p>}
          {error && <p className="harness-files-error" role="alert">{error}</p>}
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
                    title={entry.path}
                  >
                    <span aria-hidden="true">{entry.isDir ? "📁" : "📄"}</span>
                    <span className="harness-files-name">{entry.name}</span>
                    {opening === entry.path && <span aria-hidden="true">…</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {listing && (previousCursors.length > 0 || listing.nextCursor) && (
            <div className="harness-files-pagination">
              <button type="button" className="plan-node-button nodrag"
                disabled={loading || previousCursors.length === 0}
                onClick={() => loadFolder(path, previousCursors[previousCursors.length - 1] ?? null,
                  previousCursors.slice(0, -1))}>
                Previous page
              </button>
              <span>Page {previousCursors.length + 1}</span>
              <button type="button" className="plan-node-button nodrag"
                disabled={loading || !listing.nextCursor}
                onClick={() => loadFolder(path, listing.nextCursor ?? null, [...previousCursors, cursor])}>
                Next page
              </button>
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
