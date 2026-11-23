import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Tooltip } from "./Tooltip";

afterEach(() => vi.useRealTimers());

describe("Tooltip", () => {
  it("waits 500 ms to show on hover and links aria-describedby", () => {
    vi.useFakeTimers();
    render(<Tooltip label="Undo" shortcut="Ctrl+Z"><button type="button">Action</button></Tooltip>);
    const button = screen.getByRole("button", { name: "Action" });
    expect(button).not.toHaveAttribute("title");
    fireEvent.mouseEnter(button);
    act(() => vi.advanceTimersByTime(499));
    expect(screen.queryByRole("tooltip")).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    const tip = screen.getByRole("tooltip");
    expect(button).toHaveAttribute("aria-describedby", tip.id);
    expect(tip).toHaveTextContent("Undo");
    expect(tip).toHaveTextContent("Ctrl");
    expect(tip).toHaveTextContent("Z");
    fireEvent.mouseLeave(button);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("opens immediately on keyboard focus and Escape hides only the tooltip", () => {
    render(<Tooltip label="Save"><button type="button">Save control</button></Tooltip>);
    const button = screen.getByRole("button", { name: "Save control" });
    button.focus();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("hides on pointerdown, including outside its trigger", () => {
    render(<Tooltip label="Inspect"><button type="button">Inspect control</button></Tooltip>);
    screen.getByRole("button").focus();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("flips above a trigger near the viewport bottom", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
      return this.getAttribute("role") === "tooltip"
        ? { x: 0, y: 0, top: 0, bottom: 24, left: 0, right: 120, width: 120, height: 24, toJSON: () => ({}) }
        : { x: 0, y: 880, top: 880, bottom: 900, left: 0, right: 80, width: 80, height: 20, toJSON: () => ({}) };
    });
    try {
      render(<Tooltip label="Inspect"><button type="button">Inspect</button></Tooltip>);
      screen.getByRole("button").focus();
      expect(screen.getByRole("tooltip")).toHaveStyle({ top: "848px" });
    } finally {
      vi.restoreAllMocks();
    }
  });
});
