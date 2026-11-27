/** Export PNG feedback and single-flight across both toolbar entry points (#1130). */
import { ReactFlowProvider } from "@xyflow/react";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const exportMock = vi.fn();
vi.mock("../canvas/exportCanvasPng", () => ({
  exportCanvasAsPng: (...args: unknown[]) => exportMock(...args),
}));

import { AppBar } from "./AppBar";
import "./AppBarOverflowMenu";
import { OverlayProvider } from "../overlays/overlays";
import { SceneStore } from "../canvas/sceneStore";
import { toast, Toaster } from "../ui/Toast";
import type { WsTransport } from "../../lib/ws/transport";

function makeStore(nodeCount: number): SceneStore {
  const transport = { subscribe: vi.fn(), intent: vi.fn(), fireIntent: vi.fn() } as unknown as WsTransport;
  const store = new SceneStore(transport);
  const scene = { ...store.getScene(), nodes: Array.from({ length: nodeCount }, (_, i) => ({ id: `n${i}` })) };
  store.getScene = (() => scene) as typeof store.getScene;
  return store;
}

function renderAppBar(nodeCount: number) {
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <AppBar store={makeStore(nodeCount)} />
        <Toaster />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
}

async function exportItem(user: ReturnType<typeof userEvent.setup>, overflow = false) {
  if (overflow) {
    await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
    return within(await screen.findByRole("menu", { name: "More toolbar actions" }))
      .getByRole("menuitem", { name: /Export PNG/ });
  }
  await user.click(screen.getByRole("button", { name: "Export" }));
  return within(await screen.findByRole("menu", { name: "Export" })).getByRole("menuitem", { name: /Export PNG/ });
}

afterEach(() => {
  exportMock.mockReset();
  act(() => toast.clear());
});

describe("AppBar Export PNG (#1130)", () => {
  it("is disabled with a reason on an empty canvas, in both layouts", async () => {
    const user = userEvent.setup();
    renderAppBar(0);
    const item = await exportItem(user);
    expect(item).toBeDisabled();
    expect(item).toHaveAccessibleDescription(/Add a node to export a PNG/);
    await user.keyboard("{Escape}");
    const overflowItem = await exportItem(user, true);
    expect(overflowItem).toBeDisabled();
    expect(exportMock).not.toHaveBeenCalled();
  });

  it("runs one capture at a time across both entry points and reports success", async () => {
    const user = userEvent.setup();
    let finish: (outcome: string) => void = () => {};
    exportMock.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    renderAppBar(2);
    await user.click(await exportItem(user));
    expect(await screen.findByText("Exporting PNG…")).toBeInTheDocument();
    const overflowItem = await exportItem(user, true);
    expect(overflowItem).toBeDisabled();
    expect(overflowItem).toHaveAccessibleDescription(/already running/);
    expect(exportMock).toHaveBeenCalledTimes(1);
    await act(async () => { finish("exported"); });
    expect(screen.queryByText("Exporting PNG…")).toBeNull();
    expect(screen.getByText("PNG export downloaded.")).toBeInTheDocument();
  });

  it("explains a failed capture and allows another attempt", async () => {
    const user = userEvent.setup();
    exportMock.mockResolvedValueOnce("failed").mockResolvedValueOnce("exported");
    renderAppBar(1);
    await user.click(await exportItem(user));
    expect(await screen.findByText(/Couldn't export the PNG/)).toBeInTheDocument();
    await user.click(await exportItem(user));
    expect(await screen.findByText("PNG export downloaded.")).toBeInTheDocument();
    expect(exportMock).toHaveBeenCalledTimes(2);
  });

  it("explains a missing canvas instead of doing nothing", async () => {
    const user = userEvent.setup();
    exportMock.mockResolvedValueOnce("no-canvas");
    renderAppBar(1);
    await user.click(await exportItem(user));
    expect(await screen.findByText(/Couldn't find the canvas to export/)).toBeInTheDocument();
  });
});
