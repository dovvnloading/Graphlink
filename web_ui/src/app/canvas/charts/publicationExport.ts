/** Authenticated, one-shot scene intent for print-sized chart exports.
 * The existing HTTP chart endpoint cannot serve PDF; avoid navigated token URLs.
 */
import { defaultWsUrl } from "../../../lib/ws/transport";
import { downloadBlob } from "../downloadTextFile";
import type { PublicationExportOptions } from "../chartSpec";

interface ExportFile { filename: string; mimeType: string; base64: string }
interface DownloadFile { bytes: Uint8Array; filename: string; mimeType: string }
const MIME = { png: "image/png", pdf: "application/pdf", svg: "image/svg+xml" };

function decodeFile(file: ExportFile, format: PublicationExportOptions["format"]): DownloadFile {
  if (!file || typeof file.base64 !== "string" || file.base64.length > 33_000_000
      || !/^[A-Za-z0-9+/]*={0,2}$/.test(file.base64)
      || file.mimeType !== MIME[format] || typeof file.filename !== "string"
      || !/^[A-Za-z0-9_-]+\.(png|pdf|svg)$/.test(file.filename)
      || !file.filename.endsWith("." + format)) {
    throw new Error("Invalid publication export response");
  }
  const binary = atob(file.base64);
  if (!(format === "pdf" ? binary.startsWith("%PDF-")
    : format === "png" ? binary.startsWith("\x89PNG\r\n\x1a\n")
      : binary.includes("<svg"))) {
    throw new Error("Chart export file format did not match the request");
  }
  return {
    filename: file.filename, mimeType: file.mimeType,
    bytes: Uint8Array.from(binary, (char) => char.charCodeAt(0)),
  };
}

export function requestPublicationChartExport(
  nodeId: string, options: PublicationExportOptions, _chartAspectRatio: number,
): Promise<DownloadFile> {
  if (!nodeId) return Promise.reject(new Error("Chart node ID is required"));
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(defaultWsUrl());
    let settled = false;
    const timer = setTimeout(() => finish(new Error("Publication export timed out")), 120_000);
    function finish(error: Error | null, file?: DownloadFile) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      ws.close();
      if (error) reject(error);
      else if (file) resolve(file);
    }
    ws.onopen = () => ws.send(JSON.stringify({
      kind: "intent", topic: "scene", intent: "exportPublicationChart",
      args: [nodeId, options], id: "publication-export",
    }));
    ws.onmessage = ({ data }) => {
      try {
        const msg = JSON.parse(String(data));
        if (msg?.id !== "publication-export") return;
        if (msg.kind === "error") finish(new Error(msg.error || "Publication export failed"));
        else if (msg.kind === "result") finish(null, decodeFile(msg.value, options.format));
      } catch (error) {
        finish(error instanceof Error ? error : new Error("Invalid publication export response"));
      }
    };
    ws.onerror = () => finish(new Error("Chart export connection failed"));
    ws.onclose = () => finish(new Error("Chart export connection closed"));
  });
}

export async function downloadPublicationChartExport(
  nodeId: string, options: PublicationExportOptions, aspectRatio: number,
): Promise<void> {
  const { bytes, mimeType, filename } = await requestPublicationChartExport(nodeId, options, aspectRatio);
  downloadBlob(new Blob([bytes.buffer as ArrayBuffer], { type: mimeType }), filename);
}
