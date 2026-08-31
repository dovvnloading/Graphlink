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
// (or any layout at all), so it cannot verify the WIDTH half of finding #5's
// fix - which of the two copies of a button is actually visible once the bar
// has folded is a live-browser-only concern, verified separately. What
// these tests cover instead: the dead provider-mode <select> from finding #8
// is genuinely gone; every real button (both the inline copy and its
// overflow-menu duplicate) is present and calls the correct handler; the
// overflow menu opens/closes correctly; and the two sets stay in step -
// every collapsible action has a duplicate and every non-collapsible one
// does not, which is the invariant that silently breaks the CSS wiring in a
// way no visual glance at one window size would catch.
//
// The toolbar redesign added three more contracts worth pinning here,
// because each is a behavioural claim rather than a visual one:
//   - Help/Diagnostics/About moved off the bar into a Help MENU, so the
//     bar must no longer expose them as top-level buttons, and the menu
//     must actually reach all three;
//   - the zoom control is a live readout whose accessible name still says
//     what clicking it does, not just what it currently shows;
//   - keyboard hints live in `title` and nowhere else - putting them in the
//     accessible name would make a screen reader recite "Undo Ctrl+Z" and
//     would break every by-name query in this file.

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

  it("Library, Save and Settings are present as plain toolbar buttons (the three that never collapse)", () => {
    renderAppBar();
    expect(screen.getByRole("button", { name: "Library" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    // None of the three sits in a collapsible group - confirmed by absence
    // of the marker class, since "never collapses" IS "has no .appbar-tier".
    for (const label of ["Library", "Save", "Settings"]) {
      expect(
        screen.getByRole("button", { name: label }).closest(".appbar-group")?.className,
      ).not.toContain("appbar-tier");
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
        "Diagnostics",
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

    it("clicking an overlay-opening item (Pins) opens that overlay and closes the menu", async () => {
      const user = userEvent.setup();
      renderAppBar();
      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Pins" }));

      expect(screen.queryByRole("dialog")).toBeNull();
      // The inline Pins chip (still in the DOM, jsdom does not evaluate the
      // CSS that would visually hide it) now reads real open state. Pins
      // rather than About because About no longer HAS an inline copy - see
      // the Help menu block below.
      expect(screen.getByRole("button", { name: "Pins" }).className).toContain("checked");
    });

    it("the six overlay-opening overflow items (Pins/View/Plugins/About/Help/Diagnostics) carry aria-pressed, matching their inline copies' real-state contract", async () => {
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
      for (const label of ["Pins", "View", "Plugins", "About", "Help", "Diagnostics"]) {
        expect(menu.getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "false");
      }
    });

    it("the overflow menu holds a copy of every collapsible action and of nothing else", async () => {
      const user = userEvent.setup();
      renderAppBar();
      await user.click(screen.getByRole("button", { name: "More toolbar actions" }));
      const menu = screen.getByRole("dialog", { name: "More toolbar actions" });

      // Groups collapse whole and all at once, so an inline action and its
      // menu copy have to appear and disappear together (styles.css, the
      // single @container rule). Matched by substring rather than exact
      // string because one pair deliberately differs: the inline zoom
      // control is a live readout named "Reset zoom to 100%" while its menu
      // copy is plain "Reset". Disambiguating the two hits by DOM position
      // rather than by string stays correct for every label uniformly
      // instead of special-casing the one that needs it.
      const collapsible = [
        "Undo",
        "Redo",
        "Zoom In",
        "Zoom Out",
        "Reset",
        "Fit All",
        "Organize",
        "Pins",
        "Export PNG",
        "View",
        "Plugins",
        "Global Search",
        "Knowledge",
        "Builder",
        "Agent",
      ];
      for (const label of collapsible) {
        const matches = screen.getAllByRole("button", { name: new RegExp(label) });
        const inline = matches.find((el) => !menu.contains(el));
        const copy = matches.find((el) => menu.contains(el));
        expect(inline, `${label} has no inline copy`).toBeDefined();
        expect(copy, `${label} has no overflow copy`).toBeDefined();
        // The inline copy always sits in a group that CAN collapse.
        expect(inline?.closest(".appbar-group")?.className).toContain("appbar-tier");
      }

      // Help/Diagnostics/About have no inline copy at all any more - they
      // are reached from the Help menu at a normal desktop width. They still
      // need their own entry here, because the tools cluster that hosts that
      // menu is itself collapsible.
      for (const label of ["Diagnostics", "Help", "About"]) {
        expect(within(menu).getByRole("button", { name: label })).toBeInTheDocument();
      }

      // The three controls that never collapse have no duplicate, and their
      // groups carry no .appbar-tier for the CSS to act on.
      for (const label of ["Library", "Save", "Settings"]) {
        expect(within(menu).queryByRole("button", { name: label })).toBeNull();
        expect(
          screen.getByRole("button", { name: label }).closest(".appbar-group")?.className,
        ).not.toContain("appbar-tier");
      }
    });
  });
  // -- Toolbar redesign ----------------------------------------------------

  describe("Help menu", () => {
    it("Help, Diagnostics and About are no longer top-level toolbar buttons", () => {
      renderAppBar();
      for (const label of ["Help", "Diagnostics", "About"]) {
        expect(screen.queryByRole("button", { name: label })).toBeNull();
      }
      // The one control that replaced all three. Icon-only, so its
      // accessible name has to come from aria-label or it is nameless.
      expect(screen.getByRole("button", { name: "Help and diagnostics" })).toBeInTheDocument();
    });

    it("opens a menu that reaches all three destinations", async () => {
      const user = userEvent.setup();
      renderAppBar();
      const trigger = screen.getByRole("button", { name: "Help and diagnostics" });
      expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
      expect(trigger).toHaveAttribute("aria-expanded", "false");

      await user.click(trigger);
      expect(trigger).toHaveAttribute("aria-expanded", "true");
      const menu = within(screen.getByRole("dialog", { name: "Help and diagnostics" }));
      for (const label of ["Help", "Diagnostics", "About"]) {
        expect(menu.getByRole("button", { name: label })).toBeInTheDocument();
      }
    });

    it("choosing a destination replaces the menu with that surface", async () => {
      const user = userEvent.setup();
      renderAppBar();
      await user.click(screen.getByRole("button", { name: "Help and diagnostics" }));
      await user.click(
        within(screen.getByRole("dialog", { name: "Help and diagnostics" })).getByRole("button", {
          name: "About",
        }),
      );
      // OverlayProvider is single-open: opening "about" IS what closes this
      // menu, so an assertion that the menu is gone is the observable proof
      // the item dispatched - AppBar does not render AboutDialog itself.
      expect(screen.queryByRole("dialog", { name: "Help and diagnostics" })).toBeNull();
      expect(screen.getByRole("button", { name: "Help and diagnostics" })).toHaveAttribute(
        "aria-expanded",
        "false",
      );
    });

    it("the trigger reads real open state, like every other chip on the bar", async () => {
      const user = userEvent.setup();
      renderAppBar();
      const trigger = screen.getByRole("button", { name: "Help and diagnostics" });
      expect(trigger.className).not.toContain("checked");
      await user.click(trigger);
      expect(trigger.className).toContain("checked");
      await user.click(trigger);
      expect(trigger.className).not.toContain("checked");
    });
  });

  describe("zoom readout", () => {
    it("shows the live zoom level and names its action, not its value", () => {
      renderAppBar();
      // React Flow's default transform is [0, 0, 1] before any viewport
      // interaction, so 100% is the honest initial reading here.
      const zoom = screen.getByRole("button", { name: "Reset zoom to 100%" });
      expect(zoom).toHaveTextContent("100%");
      // The accessible name is the ACTION. A control named "100%" would
      // announce its current state and never say what activating it does.
      expect(zoom).toHaveAttribute("aria-label", "Reset zoom to 100%");
    });

    it("replaced the concentric-circles reset icon rather than adding to it", () => {
      renderAppBar();
      // Exactly one inline control resets the zoom; the other "Reset" in the
      // DOM belongs to the overflow menu, which is closed here.
      expect(screen.getAllByRole("button", { name: /Reset/ })).toHaveLength(1);
    });
  });

  describe("keyboard hints", () => {
    // Hints belong in the tooltip and nowhere else: an aria-label of
    // "Undo (Ctrl+Z)" makes a screen reader recite the binding on every
    // focus, and would break every by-name query in this file.
    it.each([
      ["Library", "Library (Ctrl+L)"],
      ["Save", "Save (Ctrl+S)"],
      ["Undo", "Undo (Ctrl+Z)"],
      ["Redo", "Redo (Ctrl+Shift+Z)"],
      ["Global Search", "Global Search (Ctrl+F)"],
    ])("%s names its binding in the tooltip only", (name, title) => {
      renderAppBar();
      const button = screen.getByRole("button", { name });
      expect(button).toHaveAttribute("title", title);
    });
  });
});
