import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ToggleButton } from "./ToggleButton";

describe("ToggleButton", () => {
  it("has a named toggle role and toggles internal aria-pressed with a click", () => {
    render(<ToggleButton icon="star" label="Mark favourite" />);
    const button = screen.getByRole("button", { name: "Mark favourite" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(button).toHaveClass("gl-toggle-button--pressed");
    expect(button.querySelector("svg")).toHaveClass("gl-toggle-button-pressed-icon");
  });

  it("supports controlled pressed state and reports the next state without mutating props", () => {
    const onPressedChange = vi.fn();
    const { rerender } = render(<ToggleButton pressed icon="pin" label="Pin view" onPressedChange={onPressedChange} />);
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(button);
    expect(onPressedChange).toHaveBeenCalledWith(false);
    expect(button).toHaveAttribute("aria-pressed", "true");
    rerender(<ToggleButton pressed={false} icon="pin" label="Pin view" />);
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("accepts an explicitly named icon-only toggle and a default pressed state", () => {
    render(<ToggleButton icon="eye" aria-label="Show details" defaultPressed size="sm" />);
    const button = screen.getByRole("button", { name: "Show details" });
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(button).toHaveClass("gl-toggle-button--sm");
  });

  it("supports keyboard activation and suppresses changes while disabled", async () => {
    const user = userEvent.setup();
    const onPressedChange = vi.fn();
    const onClick = vi.fn();
    const { rerender } = render(<ToggleButton label="Show panel" onPressedChange={onPressedChange} onClick={onClick} />);
    const button = screen.getByRole("button");
    button.focus();
    await user.keyboard("{Enter}");
    expect(onPressedChange).toHaveBeenCalledWith(true);
    expect(button).toHaveAttribute("aria-pressed", "true");
    rerender(<ToggleButton label="Show panel" disabled onPressedChange={onPressedChange} onClick={onClick} />);
    fireEvent.click(button);
    expect(onPressedChange).toHaveBeenCalledTimes(1);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("lets event handlers cancel the transition", () => {
    render(<ToggleButton label="Keep state" onClick={(event) => event.preventDefault()} />);
    const button = screen.getByRole("button");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it.each(["sm", "md", "lg"] as const)("renders size %s", (size) => {
    render(<ToggleButton size={size} label="Show panel" />);
    expect(screen.getByRole("button")).toHaveClass("gl-toggle-button--" + size);
  });
});
