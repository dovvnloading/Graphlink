/** One-shot publication export over the existing authenticated scene intents.
 *
 * The legacy /api/assets/chart/.../export route only understands PNG/SVG,
 * has fixed dimensions, and cannot be modified in this change's file scope.
 * Reuse its server-side renderer through a dedicated, bounded intent instead.
 * One temporary WS (same session/capability as the app transport) is closed
 * after the response; no token is sent to a navigated download URL.
 */
import { defaultWsUrl } from "../../../lib/ws/transport";
import { downloadBlob } from "../downloadTextFile";
import {
  resolvePublicationExportOptions,
  type PublicationExportOptions,
} from "../chartSpec";

interface ExportResponse {
  filename: string;
  mimeType: string;
  base64: string;
  widthMm: number;
  heightMm: number;
  dpi: number;
}

const MIME_TYPES = {
  png: "image/png",
  svg: "image/svg+xml",
  pdf: "application/pdf",
} as const;

function decodeExport(payload: ExportResponse, format: PublicationExportOptions["format"]): {
  bytes: Uint8Array;
  filename: string;
  mimeType: string;
} {
  if (!payload || typeof payload.base64 !== "string"
      || payload.base64.length > 33_000_000
      || !/^[A-Za-z0-9+/]*={0,2}$/.test(payload.base64)
      || payload.mimeType !== MIME_TYPES[format]
      || typeof payload.filename !== "string"
      || !/^[A-Za-z0-9_-]+\.(?:png|svg|pdf)$/.test(payload.filename)
      || !payload.filename.endsWith("." + format)) {
    throw new Error("Chart export returned an invalid file");
  }
  // Decode in chunks, not by spreading a multi-MB string into function args.
  const binary = atob(payload.base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const magic = format === "png" ? [137, 80, 78, 71] : format === "pdf" ? [37, 80, 68, 70] : [];
  if (!magic.every((byte, index) => bytes[index] === byte)
      || (format === "svg" && !binary.includes("<svg"))) {
    throw new Error("Chart export file format did not match the request");
  }
  return { bytes, filename: payload.filename, mimeType: payload.mimeType };
}

export function requestPublicationChartExport(
  nodeId: string, options: PublicationExportOptions, chartAspectRatio: number,
): Promise<{ bytes: Uint8Array; filename: string; mimeType: string }> {
  try {
    resolvePublicationExportOptions(options, chartAspectRatio);
  } catch (error) {
    return Promise.reject(error);
  }
  if (!nodeId) return Promise.reject(new Error("Chart node ID is required"));
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(defaultWsUrl());
    const requestId = "publication-export";
    let finished = false;
    const timeout = window.setTimeout(() => finish(new Error("Publication export timed out")), 120_000);
    function finish(error?: Error, value?: { bytes: Uint8Array; filename: string; mimeType: string }) {
      if (finished) return;
      finished = true;
      window.clearTimeout(timeout);
      socket.close();
      if (error) reject(error);
      else if (value) resolve(value);
    }
    socket.onopen = () => {
      socket.send(JSON.stringify({
        kind: "intent", topic: "scene", intent: "exportPublicationChart",
        args: [nodeId, options], id: requestId,
      }));
    };
    socket.onmessage = (event) => {
      let message: { kind?: string; id?: string; error?: string; value?: ExportResponse };
      try {
        message = JSON.parse(String(event.data)) as typeof message;
      } catch {
        finish(new Error("Invalid chart export response"));
        return;
      }
      if (!message || typeof message !== "object" || message.id !== requestId) return;
      if (message.kind === "error") {
        finish(new Error(message.error || "Publication export failed"));
      } else if (message.kind === "result") {
        try {
          finish(undefined, decodeExport(message.value as ExportResponse, options.format));
        } catch (error) {
          finish(error instanceof Error ? error : new Error("Invalid chart export"));
        }
      }
    };
    socket.onerror = () => finish(new Error("Chart export connection failed"));
    socket.onclose = () => finish(new Error("Chart export connection closed"));
  });
}

export async function downloadPublicationChartExport(
  nodeId: string, options: PublicationExportOptions, chartAspectRatio: number,
): Promise<void> {
  const result = await requestPublicationChartExport(nodeId, options, chartAspectRatio);
  // Using the Blob download utility avoids putting the capability token into
  // a URL exposed to the operating system's external browser or history.
  downloadBlob(new Blob([result.bytes.buffer as ArrayBuffer], { type: result.mimeType }), result.filename);
}
