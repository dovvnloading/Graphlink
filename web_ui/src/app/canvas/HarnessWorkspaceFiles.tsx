import { useEffect, useRef, useState } from "react";
import type { HarnessWorkspaceListing } from "./harnessWorkspaceFiles";

/** On-demand folder browser. Only relative paths are sent; the backend
 * derives the root from the current node grant on every request. */
export function HarnessWorkspaceFiles({
  onList,
  onOpen,
  onReveal,
}: {
  onList: (path: string) => Promise<HarnessWorkspaceListing>;
  onOpen: (path: string) => Promise<void>;
  onReveal: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [path, setPath] = useState("");
  const requestRef = useRef(0);
  const [listing, setListing] = useState<HarnessWorkspaceListing | null>(null);
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState("");

  // Fetch on user actions, not in an effect: this avoids an extra mount
  // request and keeps one pending directory navigation from replacing a
  // newer result if its response happens to arrive last.
  function loadFolder(nextPath: string) {
    const request = ++requestRef.current;
    setPath(nextPath);
    setLoading(true);
    setError("");
    setListing(null);
    void onList(nextPath)
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
  }

  function toggle() {
    if (expanded) {
      requestRef.current++;
      setExpanded(false);
      return;
    }
    setExpanded(true);
    loadFolder(path);
  }

  // Prevent a pending read from updating an unmounted (or rebound) agent.
  useEffect(() => () => { requestRef.current++; }, []);

  async function openFile(filePath: string) {
    setError("");
    setOpening(filePath);
    try {
      await onOpen(filePath);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Could not open this file.");
    } finally {
      setOpening(null);
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
          {listing?.truncated && (
            <p className="harness-files-note">Showing the first 300 entries. Open a subfolder to narrow the list.</p>
          )}
        </section>
      )}
    </div>
  );
}
