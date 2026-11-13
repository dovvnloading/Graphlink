import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FalMediaUploader } from "./FalMediaUploader";

class MockXHR {
  static requests: MockXHR[] = [];
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  status = 200;
  response: unknown = { url: "https://v3.fal.media/files/photo.png" };
  headers: Record<string, string> = {};
  method = "";
  url = "";
  file: File | null = null;

  constructor() { MockXHR.requests.push(this); }
  open(method: string, url: string) { this.method = method; this.url = url; }
  setRequestHeader(key: string, value: string) { this.headers[key] = value; }
  send(file: File) {
    this.file = file;
    this.onload?.();
  }
  abort() { this.onabort?.(); }
}

function setup(extra: Partial<React.ComponentProps<typeof FalMediaUploader>> = {}) {
  const onJsonChange = vi.fn();
  const onBusyChange = vi.fn();
  const user = userEvent.setup();
  render(<FalMediaUploader
    mode="image" operation="image-to-image" fields={["image_url"]}
    argumentsJson='{"seed":18}'
    disabled={false}
    onJsonChange={onJsonChange} onBusyChange={onBusyChange}
    {...extra}
  />);
  return { user, onJsonChange, onBusyChange };
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); MockXHR.requests = []; });

describe("Fal Media Studio direct reference upload", () => {
  it("requires an explicit confirmation before sending bytes to Fal", async () => {
    vi.stubGlobal("XMLHttpRequest", MockXHR);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { user, onJsonChange } = setup();
    fireEvent.change(screen.getByLabelText("Source file"), {
      target: { files: [new File(["image content"], "scene.png", { type: "image/png" })] },
    });
    await user.click(screen.getByRole("button", { name: /Upload reference/ }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(MockXHR.requests).toHaveLength(0);
    expect(onJsonChange).not.toHaveBeenCalled();
  });

  it("uploads media only through an authenticated first-party API and saves only the returned CDN URL", async () => {
    vi.stubGlobal("XMLHttpRequest", MockXHR);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { user, onJsonChange, onBusyChange } = setup();
    const file = new File(["image content"], "holiday.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Source file"), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /Upload reference/ }));
    expect(confirm).toHaveBeenCalled();
    const xhr = MockXHR.requests[0];
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/fal-media/upload");
    expect(xhr.headers["X-Fal-Media-Filename"]).toBe("holiday.png");
    expect(xhr.file).toBe(file);
    expect(onBusyChange).toHaveBeenCalledWith(true);
    expect(onBusyChange).toHaveBeenCalledWith(false);
    const json = JSON.parse(onJsonChange.mock.calls[0][0]);
    expect(json).toEqual({ seed: 18, image_url: "https://v3.fal.media/files/photo.png" });
    expect(onJsonChange.mock.calls[0][0]).not.toContain("image content");
    expect(screen.getByText(/Uploaded successfully/)).toBeInTheDocument();
  });

  it("rejects oversized files before opening an HTTP connection", async () => {
    vi.stubGlobal("XMLHttpRequest", MockXHR);
    const { user } = setup();
    const file = new File(["small"], "oversize.mp4", { type: "video/mp4" });
    Object.defineProperty(file, "size", { value: 513 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText("Source file"), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /Upload reference/ }));
    expect(screen.getByText(/no larger than 512 MiB/)).toBeInTheDocument();
    expect(MockXHR.requests).toHaveLength(0);
  });

  it("is disabled until a model is selected and never stores local data automatically", () => {
    vi.stubGlobal("XMLHttpRequest", MockXHR);
    setup({disabled: true});
    expect(screen.getByRole("button", { name: /Upload reference/ })).toBeDisabled();
    expect(MockXHR.requests).toHaveLength(0);
  });

  it("uses image, video and audio parameter names appropriate for each workflow", () => {
    vi.stubGlobal("XMLHttpRequest", MockXHR);
    const { rerender } = render(<FalMediaUploader
      mode="video" operation="video-to-video" fields={[]}
      argumentsJson="{}" disabled={false}
      onJsonChange={vi.fn()} onBusyChange={vi.fn()}
    />);
    expect(screen.getByRole("textbox", {name:"Media URL parameter"})).toHaveValue("video_url");
    rerender(<FalMediaUploader
      mode="audio" operation="audio-to-audio" fields={[]}
      argumentsJson="{}" disabled={false}
      onJsonChange={vi.fn()} onBusyChange={vi.fn()}
    />);
    expect(screen.getByRole("textbox", {name:"Media URL parameter"})).toHaveValue("audio_url");
  });
});
