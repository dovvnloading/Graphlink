import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { NodeMenu } from "./NodeMenu";

/**
 * ADR-012 stage 12.3: NodeMenu.tsx's own keyboard-completeness contract,
 * tested directly against its real API (a fixed position + onClose +
 * role="menuitem" children) rather than through any one of its 11 callers -
 * every caller shares this exact behavior since none of them add their own
 * keyboard handling (see NodeMenu.tsx's own stage-12.3 doc).
 */

function TriggerAndMenu({ items = ["First", "Second", "Third"] }: { items?: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open menu
      </button>
      {open && (
        <NodeMenu position={{ x: 10, y: 10 }} onClose={() => setOpen(false)} ariaLabel="Test menu">
          {items.map((item) => (
            // Matches every real caller's own convention (e.g.
            // ArtifactNodeMenu): the domain action, then onClose - modeled
            // here as just onClose, since this harness has no domain action
            // of its own to call first.
            <button key={item} type="button" role="menuitem" onClick={() => setOpen(false)}>
              {item}
            </button>
          ))}
        </NodeMenu>
      )}
    </div>
  );
}

async function openMenu() {
  const user = userEvent.setup();
  render(<TriggerAndMenu />);
  const trigger = screen.getByRole("button", { name: "Open menu" });
  trigger.focus();
  await user.click(trigger);
  return { user, trigger };
}

describe("NodeMenu keyboard completeness (ADR-012 stage 12.3)", () => {
  it("moves focus to the first menuitem on open", async () => {
    await openMenu();
    expect(screen.getByRole("menuitem", { name: "First" })).toHaveFocus();
  });

  it("ArrowDown moves focus to the next item, wrapping past the last", async () => {
    const { user } = await openMenu();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Second" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Third" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "First" })).toHaveFocus();
  });

  it("ArrowUp moves focus to the previous item, wrapping past the first", async () => {
    const { user } = await openMenu();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Third" })).toHaveFocus();
  });

  it("Home jumps to the first item, End jumps to the last", async () => {
    const { user } = await openMenu();
    await user.keyboard("{ArrowDown}{End}");
    expect(screen.getByRole("menuitem", { name: "Third" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(screen.getByRole("menuitem", { name: "First" })).toHaveFocus();
  });

  it("Escape closes the menu and restores focus to the trigger", async () => {
    const { user, trigger } = await openMenu();
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("activating a menuitem (which calls onClose) restores focus to the trigger", async () => {
    const { user, trigger } = await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Second" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("arrow keys outside the menu (focus elsewhere) do not move focus into it", async () => {
    const user = userEvent.setup();
    render(<TriggerAndMenu />);
    // Menu never opened - document.activeElement is <body>, well outside
    // menuRef's subtree, so the roving-focus branch must no-op entirely.
    await user.keyboard("{ArrowDown}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
