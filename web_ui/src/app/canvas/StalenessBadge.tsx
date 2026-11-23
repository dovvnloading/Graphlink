import { StatusPill } from "../ui/Badge";
import { Button } from "../ui/Button";
import "./NodeCards.css";

/** One consistent status vocabulary; the explanation stays available to AT. */
export function StalenessBadge({
  status, manualRerun = false, onRunBoundNodes, nodeKind,
}: {
  status?: unknown;
  manualRerun?: boolean;
  onRunBoundNodes?: () => void;
  nodeKind?: string;
}) {
  // Thinking traces refresh with the reply; they are not independent jobs.
  if (nodeKind === "thinking" || (status !== "out_of_date" && status !== "upstream_changed")) return null;

  const explanation = status === "upstream_changed"
    ? "Upstream inputs have changed; re-run manually."
    : manualRerun
      ? "Upstream inputs have changed; re-run manually."
      : "This reply used earlier upstream content or settings.";

  return (
    <div className="node-staleness-badge">
      <StatusPill status="Out of date" />
      <span className="node-staleness-description">{explanation}</span>
      {status === "upstream_changed" && onRunBoundNodes && (
        <Button size="sm" variant="ghost" className="node-staleness-run-bound nodrag"
          onClick={onRunBoundNodes}>Run bound nodes</Button>
      )}
    </div>
  );
}
