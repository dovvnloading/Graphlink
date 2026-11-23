import { afterEach, describe, expect, it, vi } from "vitest";
import { WsTransport } from "../lib/ws/transport";
import { toast } from "./ui/Toast";
import { GENERIC_INTENT_ERROR, INTENT_ACTIONS, registerIntentErrors, showIntentError } from "./intentErrors";

afterEach(() => {
  vi.restoreAllMocks();
  toast.clear();
});

describe("intent error toasts (UI-13b)", () => {
  function setup() {
    const fireIntent = vi.fn();
    const show = vi.spyOn(toast, "show").mockReturnValue("toast-test");
    return { transport: { fireIntent } as unknown as Pick<WsTransport, "fireIntent">, fireIntent, show };
  }

  it("passes through a domain message verbatim as error details", () => {
    const { transport, show } = setup();
    showIntentError(transport, "scene", "exportScientificWorkspaceMethods", "No verified Scientific Workbench results on this canvas");
    expect(show).toHaveBeenCalledOnce();
    expect(show).toHaveBeenCalledWith({
      variant: "error",
      title: "Couldn't export the methods.",
      details: "No verified Scientific Workbench results on this canvas",
    });
  });

  it("maps the generic failure to the log message and a working action", () => {
    const { transport, show, fireIntent } = setup();
    showIntentError(transport, "app-chat-library", "deleteChat", GENERIC_INTENT_ERROR);
    const options = show.mock.calls[0][0];
    expect(options.title).toBe("Couldn't delete the chat.");
    expect(options.variant).toBe("error");
    expect(options.details).toBe("Details were written to the log.");
    expect(options.action?.label).toBe("Open log folder");
    options.action?.onClick();
    expect(fireIntent).toHaveBeenCalledWith("diagnostics", "openLogFolder", []);
  });

  it("uses the stable fallback for unknown intents", () => {
    const { transport, show } = setup();
    showIntentError(transport, "mystery", "unknownIntent", "Try again.");
    expect(show).toHaveBeenCalledWith({
      variant: "error", title: "Couldn't complete that action.", details: "Try again.",
    });
  });

  it("does not double-toast an error already rendered by its request caller", () => {
    const { transport, show } = setup();
    for (const [topic, intent] of [
      ["knowledge", "openCitation"],
      ["diagnostics", "exportDiagnosticBundle"],
      ["app-chat-library", "importWorkspace"],
      ["scene", "previewOrganize"],
    ]) showIntentError(transport, topic, intent, GENERIC_INTENT_ERROR);
    expect(show).not.toHaveBeenCalled();
    expect(INTENT_ACTIONS["scene/exportScientificWorkspaceMethods"]).toBe("export the methods");
  });

  it("forwards every correlated error, and unsubscribe removes the listener", async () => {
    const socket: {
      send: ReturnType<typeof vi.fn>;
      close: ReturnType<typeof vi.fn>;
      onopen: null | (() => void);
      onclose: null | (() => void);
      onerror: null | (() => void);
      onmessage: null | ((event: { data: string }) => void);
    } = {
      send: vi.fn(), close: vi.fn(),
      onopen: null, onclose: null, onerror: null, onmessage: null,
    };
    const transport = new WsTransport("ws://localhost/test", { webSocketFactory: () => socket });
    const show = vi.spyOn(toast, "show").mockReturnValue("toast-test");
    transport.connect();
    socket.onopen?.();
    const unsubscribe = registerIntentErrors(transport);

    const first = transport.request("scene", "exportScientificWorkspaceMethods", []);
    const id = JSON.parse(socket.send.mock.calls[0][0] as string).id as number;
    socket.onmessage?.({ data: JSON.stringify({ kind: "error", id, error: "No verified methods." }) });
    await expect(first).rejects.toThrow("No verified methods.");
    expect(show).toHaveBeenCalledOnce();

    unsubscribe();
    const second = transport.request("scene", "exportScientificWorkspaceMethods", []);
    const secondId = JSON.parse(socket.send.mock.calls[1][0] as string).id as number;
    socket.onmessage?.({ data: JSON.stringify({ kind: "error", id: secondId, error: "No results." }) });
    await expect(second).rejects.toThrow("No results.");
    expect(show).toHaveBeenCalledOnce();
    transport.dispose();
  });
});
