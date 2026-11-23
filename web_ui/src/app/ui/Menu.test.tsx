import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { Dialog, OverlayProvider, useOverlays } from "../overlays/overlays";
import { NodeMenu } from "../canvas/NodeMenu";
import { clampMenuPosition, clampSubMenuPosition } from "../canvas/menuPosition";
import { ContextMenu, Menu, MenuButton, MenuGroup, MenuItem, MenuSeparator, SubMenu } from "./Menu";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

function ButtonHarness() {
  const select = vi.fn();
  return (
    <MenuButton label="Actions">
      <MenuGroup label="Edit">
        <MenuItem label="Copy link" icon="copy" shortcut="Ctrl+C" description="Copy the link" onSelect={select} />
        <MenuItem label="Unavailable" disabled disabledReason="Requires an active workspace" />
      </MenuGroup>
      <MenuSeparator />
      <MenuItem label="Pin" checked={false} onSelect={select} />
      <MenuItem label="Mode A" kind="radio" checked onSelect={select} />
      <MenuItem label="Delete" destructive onSelect={select} />
    </MenuButton>
  );
}

describe("kit menu structure and focus", () => {
  it("exposes trigger state, group, shortcut, disabled reason, checked roles and last-group destructive action", async () => {
    const user = userEvent.setup();
    render(<ButtonHarness />);
    const trigger = screen.getByRole("button", { name: "Actions" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);
    const menu = screen.getByRole("menu", { name: "Actions" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("group", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Ctrl+C" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Copy link" })).toHaveAccessibleDescription("Copy the link");
    const disabled = screen.getByRole("menuitem", { name: "Unavailable" });
    expect(disabled).toBeDisabled();
    expect(disabled).toHaveAccessibleDescription("Requires an active workspace");
    expect(disabled).not.toHaveAttribute("title");
    expect(screen.getByRole("menuitemcheckbox", { name: "Pin" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("menuitemradio", { name: "Mode A" })).toHaveAttribute("aria-checked", "true");
    const items = Array.from(menu.querySelectorAll('[role^="menuitem"]'));
    expect(items.map((item) => item.getAttribute("aria-label"))).toEqual(
      ["Copy link", "Unavailable", "Pin", "Mode A", "Delete"],
    );
    const destructive = screen.getByRole("menuitem", { name: "Delete" });
    expect(destructive.querySelector("svg")).toBeInTheDocument();
    expect(destructive).toHaveAttribute("data-destructive", "true");
    expect(screen.getByRole("group", { name: "Destructive actions" })).toContainElement(destructive);
    expect(within(menu).getAllByRole("separator").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("menuitem", { name: "Copy link" })).toHaveFocus();
  });

  it("roves with arrows, Home/End, type-ahead, activates with Space and returns focus", async () => {
    const user = userEvent.setup();
    const selected = vi.fn();
    render(<MenuButton label="Files">
      <MenuItem label="Alpha" onSelect={selected} />
      <MenuItem label="Beta" onSelect={selected} />
      <MenuItem label="Busy" disabled />
      <MenuItem label="Gamma" onSelect={selected} />
    </MenuButton>);
    const trigger = screen.getByRole("button", { name: "Files" });
    await user.click(trigger);
    expect(screen.getByRole("menuitem", { name: "Alpha" })).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Gamma" })).toHaveFocus();
    await user.keyboard("{Home}{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Beta" })).toHaveFocus();
    await user.keyboard("g");
    expect(screen.getByRole("menuitem", { name: "Gamma" })).toHaveFocus();
    await user.keyboard("{End}{Home}");
    expect(screen.getByRole("menuitem", { name: "Alpha" })).toHaveFocus();
    await user.keyboard("{Space}");
    expect(selected).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("activates kit checkbox and radio choices with Space and restores trigger focus", async () => {
    const user = userEvent.setup();
    const toggle = vi.fn();
    const choose = vi.fn();
    render(<MenuButton label="Choices">
      <MenuItem label="Toggle" checked={false} onSelect={toggle} />
      <MenuItem label="Choose" kind="radio" checked onSelect={choose} />
    </MenuButton>);
    const trigger = screen.getByRole("button", { name: "Choices" });
    await user.click(trigger);
    await user.keyboard("{Space}");
    expect(toggle).toHaveBeenCalledOnce();
    expect(trigger).toHaveFocus();
    await user.click(trigger);
    await user.keyboard("{ArrowDown}{Space}");
    expect(choose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("opens a submenu with ArrowRight and closes only it with ArrowLeft or Escape", async () => {
    const user = userEvent.setup();
    render(<MenuButton label="Export actions">
      <MenuItem label="Rename" />
      <SubMenu label="Export">
        <MenuItem label="PNG" />
        <MenuItem label="SVG" />
      </SubMenu>
    </MenuButton>);
    await user.click(screen.getByRole("button", { name: "Export actions" }));
    await user.keyboard("{ArrowDown}{ArrowRight}");
    expect(screen.getByRole("menu", { name: "Export" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "PNG" })).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowLeft}");
    expect(screen.queryByRole("menu", { name: "Export" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Export" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("menuitem", { name: "PNG" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "Export" })).not.toBeInTheDocument();
    expect(screen.getByRole("menu", { name: "Export actions" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export actions" })).toHaveFocus();
  });

  it("requires 150 ms of hover intent before revealing the submenu", () => {
    vi.useFakeTimers();
    render(<Menu position={{ x: 10, y: 10 }} ariaLabel="Actions" onClose={() => {}}>
      <SubMenu label="More">
        <MenuItem label="Nested action" />
      </SubMenu>
    </Menu>);
    fireEvent.mouseEnter(screen.getByRole("menuitem", { name: "More" }));
    act(() => vi.advanceTimersByTime(149));
    expect(screen.queryByRole("menu", { name: "More" })).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole("menu", { name: "More" })).toBeInTheDocument();
  });
});

describe("context triggers, position and legacy children", () => {
  it("opens at a right-click position and accepts keyboard ContextMenu and Shift+F10", async () => {
    const user = userEvent.setup();
    render(<ContextMenu ariaLabel="Context actions" trigger={<button type="button">Target node</button>}>
      <MenuItem label="Rename" />
    </ContextMenu>);
    const target = screen.getByRole("button", { name: "Target node" });
    fireEvent.contextMenu(target, { clientX: 220, clientY: 180 });
    expect(screen.getByRole("menu", { name: "Context actions" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    target.focus();
    await user.keyboard("{Shift>}{F10}{/Shift}");
    expect(screen.getByRole("menu", { name: "Context actions" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    target.focus();
    await user.keyboard("{ContextMenu}");
    expect(screen.getByRole("menu", { name: "Context actions" })).toBeInTheDocument();
  });

  it("keeps raw node buttons focusable and activatable without changing node view code", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const activate = vi.fn();
    render(<NodeMenu position={{ x: 15, y: 20 }} onClose={onClose} ariaLabel="Canvas">
      <button type="button" role="menuitem">First</button>
      <button type="button" role="menuitem" disabled>Disabled</button>
      <button type="button" role="menuitemcheckbox" aria-checked="true" onClick={activate}>Flag</button>
    </NodeMenu>);
    expect(screen.getByRole("menuitem", { name: "First" })).toHaveFocus();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(screen.getByRole("menuitemcheckbox", { name: "Flag" })).toHaveFocus();
    expect(activate).toHaveBeenCalledOnce();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("activates legacy raw menuitem roles with Space and prevents page scrolling", async () => {
    const user = userEvent.setup();
    const plain = vi.fn();
    const toggle = vi.fn();
    const select = vi.fn();
    render(<NodeMenu position={{ x: 10, y: 10 }} onClose={() => {}} ariaLabel="Legacy actions">
      <button type="button" role="menuitem" onClick={plain}>Plain</button>
      <button type="button" role="menuitemcheckbox" aria-checked="false" onClick={toggle}>Flag</button>
      <button type="button" role="menuitemradio" aria-checked="true" onClick={select}>Option</button>
    </NodeMenu>);
    const first = screen.getByRole("menuitem", { name: "Plain" });
    expect(first).toHaveFocus();
    expect(fireEvent.keyDown(first, { key: "Space", code: "Space", cancelable: true })).toBe(false);
    expect(plain).toHaveBeenCalledOnce();
    await user.keyboard("{ArrowDown}");
    const checkbox = screen.getByRole("menuitemcheckbox", { name: "Flag" });
    expect(checkbox).toHaveFocus();
    expect(fireEvent.keyDown(checkbox, { key: " ", code: "Space", cancelable: true })).toBe(false);
    expect(toggle).toHaveBeenCalledOnce();
    await user.keyboard("{ArrowDown}");
    const radio = screen.getByRole("menuitemradio", { name: "Option" });
    expect(radio).toHaveFocus();
    expect(fireEvent.keyDown(radio, { key: "Spacebar", code: "Space", cancelable: true })).toBe(false);
    expect(select).toHaveBeenCalledOnce();
  });

  it("flips and clamps pointer and submenu placement to the viewport", () => {
    expect(clampMenuPosition({ x: 490, y: 390 }, { width: 200, height: 180 }, { width: 500, height: 400 }))
      .toEqual({ x: 290, y: 210 });
    expect(clampMenuPosition({ x: -40, y: -50 }, { width: 200, height: 180 }, { width: 500, height: 400 }))
      .toEqual({ x: 8, y: 8 });
    const anchor = { left: 440, top: 380, right: 470, bottom: 400, width: 30, height: 20 };
    expect(clampSubMenuPosition(anchor, { width: 150, height: 160 }, { width: 500, height: 400 }))
      .toEqual({ x: 286, y: 232 });
  });

  it("measures the portal and clamps its fixed coordinates", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { x: 0, y: 0, top: 0, left: 0, width: 210, height: 140, right: 210, bottom: 140, toJSON: () => ({}) };
    });
    render(<Menu position={{ x: window.innerWidth - 3, y: window.innerHeight - 3 }}
      ariaLabel="Edge" onClose={() => {}}><MenuItem label="One" /></Menu>);
    const menu = screen.getByRole("menu", { name: "Edge" });
    expect(menu).toHaveStyle({
      left: (window.innerWidth - 210 - 8) + "px",
      top: (window.innerHeight - 140 - 8) + "px",
    });
  });

  it("preserves original NodeMenu flip placement without extra kit clamping", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { x: 0, y: 0, top: 0, left: 0, width: 210, height: 140, right: 210, bottom: 140, toJSON: () => ({}) };
    });
    const item = <button type="button" role="menuitem">One</button>;
    const { rerender } = render(<NodeMenu position={{ x: window.innerWidth - 3, y: window.innerHeight - 3 }}
      ariaLabel="Canvas edge" onClose={() => {}}>{item}</NodeMenu>);
    const menu = screen.getByRole("menu", { name: "Canvas edge" });
    expect(menu).toHaveStyle({
      left: (window.innerWidth - 213) + "px",
      top: (window.innerHeight - 143) + "px",
    });
    // The original NodeMenu did not nudge an unflipped node menu away from (0, 0).
    rerender(<NodeMenu position={{ x: 2, y: 3 }} ariaLabel="Canvas edge" onClose={() => {}}>{item}</NodeMenu>);
    expect(menu).toHaveStyle({ left: "2px", top: "3px" });
  });
});

function DialogHarness() {
  const overlays = useOverlays();
  const [last, setLast] = useState("");
  return <>
    <button type="button" onClick={() => overlays.open("example-dialog", "dialog")}>Open dialog</button>
    <Dialog name="example-dialog" title="Example dialog">
      <MenuButton label="Actions">
        <MenuItem label="Inspect" onSelect={() => setLast("inspected")} />
      </MenuButton>
      <span>{last}</span>
    </Dialog>
  </>;
}

describe("overlay Escape ordering", () => {
  it("dismisses a nested menu without dismissing its parent dialog", async () => {
    const user = userEvent.setup();
    render(<OverlayProvider><DialogHarness /></OverlayProvider>);
    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(screen.getByRole("dialog", { name: "Example dialog" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions" }));
    expect(screen.getByRole("menu", { name: "Actions" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Example dialog" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Example dialog" })).not.toBeInTheDocument();
  });
});
