import { formatBytes, formatCount, formatDuration, formatRelativeTime, formatTokens } from "../../format";
import type { GallerySection } from "../Gallery";

function Formatters() {
  const now = Date.UTC(2026, 10, 22, 12, 0);
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Number and time examples</h3>
        <div className="gl-gallery-control-row">
          <code>{formatCount(1234567)}</code>
          <code>{formatDuration(763)}</code>
          <code>{formatDuration(0.4)}</code>
          <code>{formatBytes(2.5 * 1024 ** 3)}</code>
          <code>{formatTokens(42700)}</code>
          <code>{formatRelativeTime(now - 2 * 3600 * 1000, now)}</code>
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "Formatters", order: 37, Component: Formatters };
export default section;
