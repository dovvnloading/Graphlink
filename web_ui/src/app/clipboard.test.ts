/** The shared Copy contract (#1147): success only after a write, explained failures. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./clipboard";
import { toast } from "./ui/Toast";

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true, writable: true });
}

afterEach(() => {
  vi.restoreAllMocks();
  setClipboard(undefined);
});

describe("copyText", () => {
  it("resolves true after a successful write, announcing only when asked", async () => {
    const show = vi.spyOn(toast, "show");
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    await expect(copyText("hello", "the message")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
    expect(show).not.toHaveBeenCalled();
    await expect(copyText("hello", "the message", { announce: true })).resolves.toBe(true);
    expect(show).toHaveBeenCalledWith({ title: "Copied the message.", variant: "success" });
  });

  it("explains a denied write instead of only logging it", async () => {
    const show = vi.spyOn(toast, "show");
    vi.spyOn(console, "error").mockImplementation(() => {});
    setClipboard({ writeText: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")) });
    await expect(copyText("x", "the code", { announce: true })).resolves.toBe(false);
    expect(show).toHaveBeenCalledTimes(1);
    expect(show.mock.calls[0][0]).toMatchObject({ variant: "error", title: expect.stringMatching(/Couldn't copy the code\. .*Ctrl\+C/) });
  });

  it("explains an absent Clipboard API instead of silently doing nothing or throwing", async () => {
    const show = vi.spyOn(toast, "show");
    setClipboard(undefined);
    await expect(copyText("x", "the document")).resolves.toBe(false);
    expect(show.mock.calls[0][0]).toMatchObject({ variant: "error", title: expect.stringMatching(/isn't available here/) });
  });
});
