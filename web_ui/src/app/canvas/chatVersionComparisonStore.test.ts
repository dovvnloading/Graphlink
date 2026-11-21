import { describe, expect, it, vi } from "vitest";
import type { WsTransport } from "../../lib/ws/transport";
import { SceneStore } from "./sceneStore";

describe("read-only chat version comparison transport", () => {
  const pair = {
    left: { index: 0, content: "old", provider: "A", model: "a1", createdAt: "2026-01-01T00:00:00Z" },
    right: { index: 2, content: "new", provider: null, model: null, createdAt: "" },
    versionCount: 3,
  };

  it("requests only the chosen pair without firing setChatVersion", async () => {
    const request = vi.fn().mockResolvedValue(pair);
    const fireIntent = vi.fn();
    const store = new SceneStore({ request, fireIntent } as unknown as WsTransport);
    await expect(store.compareChatVersions("node-1", 0, 2)).resolves.toEqual(pair);
    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith("scene", "compareChatVersions", ["node-1", 0, 2]);
    expect(fireIntent).not.toHaveBeenCalled();
  });

  it("rejects malformed server payloads instead of displaying untrusted data", async () => {
    const request = vi.fn().mockResolvedValue({ ...pair, right: { ...pair.right, content: 27 } });
    const store = new SceneStore({ request } as unknown as WsTransport);
    await expect(store.compareChatVersions("node-1", 0, 2)).rejects.toThrow("Invalid chat version response");
  });
});
