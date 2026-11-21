/** A shape and explicit text carry status without any color requirement. */
import "./NodeCards.css";

export function StalenessBadge({ status }: { status?: unknown }) {
  const label = status === "out_of_date" ? "Out of date"
    : status === "upstream_changed" ? "Upstream changed" : null;
  if (!label) return null;
  return (
    <div className="node-staleness-badge" role="status">
      <svg aria-hidden="true" viewBox="0 0 20 20" fill="none"
        stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
        <path d="M10 2.5 18 17H2L10 2.5Z" />
        <path d="M10 7v4.5M10 14.5h.01" strokeLinecap="round" />
      </svg>
      <span>{label}</span>
    </div>
  );
}
