import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadBlob } from "../downloadTextFile";
import { downloadPublicationChartExport, requestPublicationChartExport } from "./publicationExport";
import type { PublicationExportOptions } from "../chartSpec";

vi.mock("../downloadTextFile", () => ({ downloadBlob: vi.fn() }));

const options: PublicationExportOptions = {
  format: "png", size: "single", dpi: 300,
  colorblindSafe: false, grayscaleSafe: false,
};

class FakeSocket {
  static instances: FakeSocket[] = [];
  public onopen: ((event: Event) => void) | null = null;
  public onmessage: ((event: MessageEvent) => void) | null = null;
  public onerror: ((event: Event) => void) | null = null;
  public onclose: ((event: CloseEvent) => void) | null = null;
  public sent = "";
  public closed = false;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(content: string) { this.sent = content; }
  close() { this.closed = true; }
  respond(value: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(value) }));
  }
  open() { this.onopen?.(new Event("open")); }
}

describe("authenticated publication export request", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.mocked(downloadBlob).mockClear();
    FakeSocket.instances = [];
  });

  it("uses a one-shot authenticated WS intent without navigating to a token-bearing URL", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const result = requestPublicationChartExport("chart-1", options, 0.7);
    const socket = FakeSocket.instances[0];
    expect(socket.url).toContain("/ws?session=default");
    socket.open();
    const sent = JSON.parse(socket.sent);
    expect(sent).toEqual({
      kind: "intent", topic: "scene", intent: "exportPublicationChart",
      args: ["chart-1", options], id: "publication-export",
    });
    socket.respond({ kind: "result", id: "publication-export", value: {
      filename: "Chart.png", mimeType: "image/png",
      base64: btoa("\x89PNG\r\n\x1a\nDATA"), widthMm: 85, heightMm: 59.5, dpi: 300,
    } });
    const file = await result;
    expect(file.filename).toBe("Chart.png");
    expect(Array.from(file.bytes.slice(0, 4))).toEqual([137, 80, 78, 71]);
    expect(socket.closed).toBe(true);
  });

  it("turns server and malformed file failures into rejected promises", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const failed = requestPublicationChartExport("chart-2", options, 0.7);
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].respond({ kind: "error", id: "publication-export", error: "unknown chart" });
    await expect(failed).rejects.toThrow("unknown chart");

    const malformed = requestPublicationChartExport("chart-2", options, 0.7);
    FakeSocket.instances[1].open();
    FakeSocket.instances[1].respond({ kind: "result", id: "publication-export", value: {
      filename: "Chart.png", mimeType: "image/png", base64: btoa("not a PNG"),
    } });
    await expect(malformed).rejects.toThrow(/format did not match/);
  });

  it("downloads PDF with a vector file MIME and closes the socket", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const action = downloadPublicationChartExport("chart-3", { ...options, format: "pdf", size: "double" }, 0.6);
    const socket = FakeSocket.instances[0];
    socket.open();
    socket.respond({ kind: "result", id: "publication-export", value: {
      filename: "Publication.pdf", mimeType: "application/pdf", base64: btoa("%PDF-1.4\n"),
    } });
    await action;
    expect(vi.mocked(downloadBlob)).toHaveBeenCalledOnce();
    const blob = vi.mocked(downloadBlob).mock.calls[0][0];
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("application/pdf");
    expect(vi.mocked(downloadBlob).mock.calls[0][1]).toBe("Publication.pdf");
    expect(socket.closed).toBe(true);
  });

  it("does not allocate a socket for invalid size or DPI", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    await expect(requestPublicationChartExport("chart-1",
      { ...options, size: "custom", widthMm: 300, heightMm: 300, dpi: 600 },
      1,
    )).rejects.toThrow(/megapixel/);
    expect(FakeSocket.instances).toHaveLength(0);
  });
});
