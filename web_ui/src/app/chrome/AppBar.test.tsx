import { ReactFlowProvider } from "@xyflow/react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppBar } from "./AppBar";
import { OverlayProvider } from "../overlays/overlays";
import { SceneStore } from "../canvas/sceneStore";
import type { WsTransport } from "../../lib/ws/transport";

// R8a (UI/UX issue list findings #5 and #8): this is the first dedicated
// test file for AppBar.tsx. jsdom does not implement CSS @container queries
// (or any layout at all), so it cannot verify the RESPONSIVE half of finding
// #5's fix - which of the two copies of a button is actually visible at a
// given width is a live-browser-only concern, verified separately. What
// these tests cover instead: the dead provider-mode <select> from finding #8
// is genuinely gone; every real button (both the inline copy and its
// overflow-menu duplicate) is present and calls the correct handler; the
// overflow menu opens/closes correctly; and every duplicated pair carries
// the same data-tier value, since a mismatch there would silently break the
// CSS wiring in a way no visual glance at one window size would catch.

function makeStore(): SceneStore {
  // ADR-003 stage 3.1: fireIntent is the transport method SceneStore's own
  // mutating intent call sites actually use now - see sceneStore.ts's own
  // module doc.
  const transport = { subscribe: vi.fn(), intent: vi.fn(), fireIntent: vi.fn() } as unknown as WsTransport;
  return new SceneStore(transport);
}

function renderAppBar(store = makeStore()) {
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <AppBar store={store} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  return { store };
}

describe("AppBar", () => {
  it("no longer renders the provider-mode select (finding #8) - Settings is the only provider surface", () => {
    renderAppBar();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByText("Ollama (Local)")).toBeNull();
    expect(screen.queryByTitle("Switching provider modes isn't available yet")).toBeNull();
  });

  it("Library, Save and Settings are present as plain toolbar buttons (the tier that never collapses)", () => {
    renderAppBar();
    expect(screen.getByRole("button", { name: "Library" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    // None of the three ever has a data-tier - confirmed by absence, not a
    // positive class check, since "never collapses" IS "has no tier".
    for (const label of ["Library", "Save", "Settings"]) {
      expect(screen.getByRole("button", { name: label })).not.toHaveAttribute("data-tier");
    }
  });

  it("Save calls store.saveChat", async () => {
    const user = userEvent.setup();
    const { store } = renderAppBar();
    const spy = vi.spyOn(store, "saveChat");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(spy).toHaveBeenCalledOnce();
  });

  it("Settings toggles real overlay-open state, not latched click state (audit B6)", async () => {
    const user = userEvent.setup();
    renderAppBar();
    const settings = screen.getByRole("button", { name: "Settings" });
    expect(settings.className).not.toContain("checked");
    await user.click(settings);
    expect(settings.className).toContain("checked");
    await user.click(settings);
    expect(settings.className).not.toContain("checked");
  });

  describe("overflow menu (finding #5)", () => {
    it("does not render until the trigger is clicked", () => {
      renderAppBar();
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("opens on trigger click and closes on a second click", async () => {
      const user = userEvent.setup();
      renderAppBar();
      const trigger = screen.getByRole("button", { name: "More toolbar actions" });
      expect(trigger).toHaveAttribute("aria-expanded", "false");
      // Popover (overlays.tsx) renders role="dialog", not role="menu" - the
      // trigger's aria-haspopup must say so too, or a screen reader
      // announces a control that opens something other than what it does.
      expect(trigger).toHaveAttribute("aria-haspopup", "dialog");

      await user.click(trigger);
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(trigger).toHaveAttribute("aria-expanded", "true");

      await user.click(trigger);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(trigger).toHaveAttribute("aria-expanded", "false");
    });

    it("closes on Escape", async () => {
      const user = userEvent.setup();
      renderAppBar();
      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("contains a duplicate of every collapsible button", async () => {
      const user = userEvent.setup();
      renderAppBar();

      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      const menuItems = within(screen.getByRole("dialog"));

      for (const label of [
        "Export PNG",
        "Pins",
        "Organize",
        "View",
        "Plugins",
        "Zoom In",
        "Zoom Out",
        "Reset",
        "Fit All",
        "About",
        "Help",
      ]) {
        expect(menuItems.getByRole("button", { name: label })).toBeInTheDocument();
      }
    });

    // R8a: every "plain" action (doesn't open an overlay of its own) is
    // tested individually, not just one "representative" action - Export
    // PNG's overflow copy shipped without its closeOverflow() call at
    // first, and a test that only checked Organize passed anyway, since
    // Organize happened to be correct. One shared assertion per action
    // closes that exact gap for good, rather than re-opening it the next
    // time a plain action is added.
    it.each(["Export PNG", "Organize", "Zoom In", "Zoom Out", "Reset", "Fit All"])(
      "clicking the overflow copy of a plain action (%s) closes the menu",
      async (label) => {
        const user = userEvent.setup();
        renderAppBar();
        await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
        await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: label }));
        expect(screen.queryByRole("dialog")).toBeNull();
      },
    );

    it("Organize's overflow copy calls store.organizeNodes", async () => {
      const user = userEvent.setup();
      const { store } = renderAppBar();
      const organizeSpy = vi.spyOn(store, "organizeNodes");
      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Organize" }));
      expect(organizeSpy).toHaveBeenCalledOnce();
    });

    it("clicking an overlay-opening item (About) opens that overlay and closes the menu", async () => {
      const user = userEvent.setup();
      renderAppBar();
      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "About" }));

      expect(screen.queryByRole("dialog")).toBeNull();
      // The inline About chip (still in the DOM, jsdom does not evaluate
      // the CSS that would visually hide it) now reads real open state.
      expect(screen.getByRole("button", { name: "About" }).className).toContain("checked");
    });

    it("the five overlay-opening overflow items (Pins/View/Plugins/About/Help) carry aria-pressed, matching their inline copies' real-state contract", async () => {
      // Cannot be driven to aria-pressed="true" through normal interaction
      // in this test: OverlayProvider is single-open, so the instant any of
      // these becomes the open surface, "toolbar-overflow" stops being it
      // and this whole menu unmounts. This only pins the attribute exists
      // (defaulting false) rather than being silently absent, the way it
      // was before - see AppBar.tsx's own comment on why it's kept anyway.
      const user = userEvent.setup();
      renderAppBar();
      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      const menu = within(screen.getByRole("dialog"));
      for (const label of ["Pins", "View", "Plugins", "About", "Help"]) {
        expect(menu.getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "false");
      }
    });

    it("every overflow-menu item shares its data-tier with the matching inline button, and only the always-visible three (Library/Save/Settings) have no overflow duplicate at all", async () => {
      const user = userEvent.setup();
      renderAppBar();
      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      const menu = screen.getByRole("dialog");

      const pairs: [string, string][] = [
        ["Export PNG", "1"],
        ["Pins", "2"],
        ["Organize", "2"],
        ["View", "2"],
        ["Plugins", "2"],
        ["Zoom In", "3"],
        ["Zoom Out", "3"],
        ["Reset", "3"],
        ["Fit All", "3"],
        ["About", "1"],
        ["Help", "1"],
      ];
      for (const [label, tier] of pairs) {
        // Every duplicated pair shares its exact label except Plugins (the
        // inline button's accessible name includes its chevron span,
        // "Plugins ▼", while its overflow duplicate is plain "Plugins" - a
        // real, intentional difference, see AppBar.tsx) - so match by
        // substring, then disambiguate the two hits by DOM position rather
        // than by string, which stays correct for every label uniformly
        // rather than special-casing the one that needs it.
        const matches = screen.getAllByRole("button", { name: new RegExp(label) });
        const inline = matches.find((el) => !menu.contains(el));
        const overflowItem = matches.find((el) => menu.contains(el));
        expect(inline).toHaveAttribute("data-tier", tier);
        expect(overflowItem).toHaveAttribute("data-tier", tier);
      }

      for (const label of ["Library", "Save", "Settings"]) {
        expect(within(menu).queryByRole("button", { name: label })).toBeNull();
      }
    });
  });
});
