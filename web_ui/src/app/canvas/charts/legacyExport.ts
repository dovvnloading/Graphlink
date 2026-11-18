/** Legacy asset-sized chart export API kept for compatible callers.
 * The chart node now uses publicationExport.ts for selectable print sizes.
 */
import { authHeaders } from "../../../lib/auth/token";
import { downloadBlob } from "../downloadTextFile";

/** The dedicated export endpoint - a real matplotlib re-render server-side
 * (PNG at 3x resolution via graphlink_chart_rendering.py's dpi_scale, or
 * SVG as vector - see render_chart_png/render_chart_svg), never anything
 * cached. `session=default` mirrors this app's single-session-per-window
 * assumption everywhere else (see lib/ws/transport.ts's own defaultWsUrl
 * default parameter). */
export function chartExportUrl(nodeId: string, format: "png" | "svg" = "png"): string {
  // Deliberately NO token in this URL. It used to be withAuthToken(...) and
  // to be used as an <a href target="_blank">, which leaked the live
  // capability token off the machine: in the pywebview/WebView2 shell a
  // target=_blank click raises NewWindowRequested, whose default handler
  // opens the fully-resolved URL in the OS default browser - so the token
  // landed in that browser's address bar, history and download manager, and
  // was copyable straight out of the link's context menu. A synced browser
  // history uploads it. That token authorizes every /api intent, including
  // the code-execution approval gate, so this was the one place the "the
  // token never leaves this window" property (see lib/auth/token.ts) broke.
  //
  // The fetch below sends it as an Authorization HEADER instead, and the
  // bytes are saved from a blob - nothing user-visible carries the secret.
  return `/api/assets/chart/${nodeId}/export?session=default&fmt=${format}`;
}

/** Fetches a chart export with the capability token in a header and saves
 * the resulting bytes as a file via downloadTextFile.ts's shared
 * downloadBlob; the only difference from a plain text/image export is that
 * the bytes come from an authenticated request rather than from memory.
 * Legacy callers receive non-throwing best-effort downloads. */
export async function downloadChartExport(nodeId: string, format: "png" | "svg"): Promise<void> {
  try {
    const response = await fetch(chartExportUrl(nodeId, format), { headers: authHeaders() });
    if (!response.ok) throw new Error(`Chart export failed (${response.status})`);
    const blob = await response.blob();
    // The server names the file from the chart's own title
    // (backend/assets.py's _sanitize_chart_filename) and sends it in
    // Content-Disposition - the same header the old navigated link relied on,
    // so honoring it here keeps the downloaded filename identical.
    const filename = filenameFromContentDisposition(response.headers.get("content-disposition"))
      || `chart.${format}`;
    downloadBlob(blob, filename);
  } catch (error) {
    console.error("[chart-node] Chart export failed:", error);
  }
}

/** Pulls `filename="..."` out of a Content-Disposition header. Exported for
 * direct unit testing. Returns "" when the header is absent or unparseable,
 * letting the caller fall back to its own default. */
export function filenameFromContentDisposition(header: string | null): string {
  if (!header) return "";
  const match = /filename\s*=\s*"([^"]+)"/i.exec(header) ?? /filename\s*=\s*([^;]+)/i.exec(header);
  return match ? match[1].trim() : "";
}

