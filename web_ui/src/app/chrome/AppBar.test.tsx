import { ReactFlowProvider } from "@xyflow/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as download from "../canvas/downloadTextFile";
import { AppBar } from "./AppBar";
// Preload the lazy overflow chunk; its import timing is not under test.
import "./AppBarOverflowMenu";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { SceneStore } from "../canvas/sceneStore";
import type { WsTransport } from "../../lib/ws/transport";

function makeStore(): SceneStore {
  const transport = { subscribe: vi.fn(), intent: vi.fn(), fireIntent: vi.fn() } as unknown as WsTransport;
  return new SceneStore(transport);
}

function ActiveOverlayProbe() {
  const overlays = useOverlays();
  return <output data-testid="active-overlay">{overlays.openSurface ?? ""}</output>;
}
function renderAppBar(store = makeStore()) {
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <AppBar store={store} />
        <ActiveOverlayProbe />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  return { store };
}
async function openExportMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Export" }));
  return screen.findByRole("menu", { name: "Export" });
}
async function openOverflowMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
  return screen.findByRole("menu", { name: "More toolbar actions" });
}

describe("AppBar (UI-14 kit migration)", () => {
  it("retains exact accessible names, grouping and the never-collapsible commands", () => {
    renderAppBar();
    const names = [
      "Library", "Save", "Undo", "Redo", "Organize", "Pins", "View", "Plugins",
      "Global Search", "Knowledge", "Builder", "Agent", "Help and diagnostics",
      "Settings", "More toolbar actions",
    ];
    for (const name of names) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    for (const name of ["Library", "Save", "Settings"]) {
      expect(screen.getByRole("button", { name }).closest(".appbar-group"))
        .not.toHaveClass("appbar-tier");
    }
    for (const name of ["Undo", "Redo", "Organize", "Pins", "View", "Plugins",
      "Global Search", "Knowledge", "Builder", "Agent"]) {
      expect(screen.getByRole("button", { name }).closest(".appbar-tier")).toBeInTheDocument();
    }
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("uses kit buttons, 16px md icon-buttons and tooltip shortcuts, not title attributes", () => {
    renderAppBar();
    for (const name of ["Undo", "Redo", "Organize", "Pins", "Global Search", "Settings"]) {
      expect(screen.getByRole("button", { name })).toHaveClass("gl-icon-button--md");
    }
    expect(screen.getByRole("button", { name: "Save" })).toHaveClass("gl-button--ghost");
    const undo = screen.getByRole("button", { name: "Undo" });
    expect(undo).toHaveAttribute("aria-keyshortcuts", "Ctrl+Z");
    fireEvent.focus(undo);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Ctrl+Z");
    expect(screen.getByRole("toolbar").querySelector("[title]")).toBeNull();
  });

  it("Save dispatches through SceneStore", async () => {
    const user = userEvent.setup();
    const { store } = renderAppBar();
    const spy = vi.spyOn(store, "saveChat");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(spy).toHaveBeenCalledOnce();
  });

  it("Library, Pins and Settings reflect the overlay provider's real pressed state", async () => {
    const user = userEvent.setup();
    renderAppBar();
    for (const name of ["Library", "Pins", "Settings"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toHaveAttribute("aria-pressed", "false");
      await user.click(button);
      expect(button).toHaveAttribute("aria-pressed", "true");
      await user.click(button);
      expect(button).toHaveAttribute("aria-pressed", "false");
    }
  });

  it("exports server-verified methods from Export menu using the kit menuitem", async () => {
    const user = userEvent.setup();
    const store = makeStore();
    const markdown = "# Graphlink workspace — scientific methods\n\n## Result\n";
    const request = vi.spyOn(store, "exportWorkspaceMethods").mockResolvedValue(markdown);
    const saved = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      renderAppBar(store);
      const menu = await openExportMenu(user);
      expect(within(menu).queryByRole("button", { name: /Export methods/ })).toBeNull();
      await user.click(within(menu).getByRole("menuitem", { name: "Export methods…" }));
      await waitFor(() => expect(saved).toHaveBeenCalledOnce());
      expect(request).toHaveBeenCalledOnce();
      expect(saved.mock.calls[0][1]).toBe("graphlink-workspace-methods.md");
      expect(await saved.mock.calls[0][0].text()).toBe(markdown);
      expect(screen.queryByRole("menu", { name: "Export" })).toBeNull();
    } finally {
      saved.mockRestore();
    }
  });

  it("leaves methods export enabled on an empty canvas so backend can explain any refusal", async () => {
    const user = userEvent.setup();
    const store = makeStore();
    const request = vi.spyOn(store, "exportWorkspaceMethods")
      .mockRejectedValue(new Error("No verified results on this canvas."));
    const saved = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      renderAppBar(store);
      const menu = await openExportMenu(user);
      const action = within(menu).getByRole("menuitem", { name: "Export methods…" });
      expect(action).toBeEnabled();
      await user.click(action);
      await waitFor(() => expect(request).toHaveBeenCalledOnce());
      expect(saved).not.toHaveBeenCalled();
    } finally {
      saved.mockRestore();
    }
  });

  it("keeps Export PNG and methods as menu items instead of independent toolbar buttons", async () => {
    const user = userEvent.setup();
    renderAppBar();
    expect(screen.queryByRole("button", { name: "Export PNG" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Export methods" })).toBeNull();
    const menu = await openExportMenu(user);
    expect(within(menu).getByRole("menuitem", { name: "Export PNG…" })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Export methods…" })).toBeInTheDocument();
  });

  it("the overflow uses the same kit menu, duplicate actions and closes after a command", async () => {
    const user = userEvent.setup();
    renderAppBar();
    const trigger = screen.getByRole("button", { name: "More toolbar actions" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    const menu = await openOverflowMenu(user);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    for (const name of ["Undo", "Redo", "Organize", "Pins", "Export PNG…",
      "Export methods…", "View", "Plugins", "Global Search", "Knowledge",
      "Builder", "Agent", "Help", "Keyboard shortcuts", "Diagnostics", "About"]) {
      expect(within(menu).getByRole("menuitem", { name })).toBeInTheDocument();
    }
    expect(within(menu).queryByRole("menuitem", { name: "Save" })).toBeNull();
    expect(within(menu).queryByRole("menuitem", { name: "Settings" })).toBeNull();
    await user.click(within(menu).getByRole("menuitem", { name: "Organize" }));
    expect(screen.queryByRole("menu", { name: "More toolbar actions" })).toBeNull();
    expect(screen.getByTestId("active-overlay")).toHaveTextContent("organize");
  });

  it("overflow Escape and keyboard focus restore to trigger", async () => {
    const user = userEvent.setup();
    renderAppBar();
    await openOverflowMenu(user);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "More toolbar actions" })).toBeNull();
    expect(screen.getByRole("button", { name: "More toolbar actions" })).toHaveFocus();
  });

  it("Help and diagnostics is a kit menu with all four destinations", async () => {
    const user = userEvent.setup();
    renderAppBar();
    const help = screen.getByRole("button", { name: "Help and diagnostics" });
    expect(help).toHaveAttribute("aria-haspopup", "menu");
    await user.click(help);
    const menu = screen.getByRole("menu", { name: "Help and diagnostics" });
    for (const name of ["Help", "Keyboard shortcuts", "Diagnostics", "About"]) {
      expect(within(menu).getByRole("menuitem", { name })).toBeInTheDocument();
    }
    await user.click(within(menu).getByRole("menuitem", { name: "About" }));
    expect(screen.getByTestId("active-overlay")).toHaveTextContent("about");
  });
});
