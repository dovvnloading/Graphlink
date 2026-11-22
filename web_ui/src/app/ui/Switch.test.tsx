import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Switch } from "./Switch";

describe("Switch", () => {
  it("uses a native checkbox with switch semantics and works from the keyboard", async () => {
    const user = userEvent.setup();
    render(<Switch label="Enable sync" description="All devices" />);
    const toggle = screen.getByRole("switch", { name: "Enable sync" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    toggle.focus();
    await user.keyboard(" ");
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).toHaveAccessibleDescription("All devices");
  });
  it("supports controlled checked values, callbacks, and errors", async () => {
    const user = userEvent.setup(), onCheckedChange = vi.fn();
    const view = render(<Switch label="Publish" checked={false} onCheckedChange={onCheckedChange} error="Not permitted" />);
    const toggle = screen.getByRole("switch", { name: "Publish" });
    expect(toggle).toHaveAttribute("aria-invalid", "true");
    await user.click(toggle);
    expect(onCheckedChange).toHaveBeenCalledWith(true);
    expect(toggle).not.toBeChecked();
    view.rerender(<Switch label="Publish" checked onCheckedChange={onCheckedChange} />);
    expect(toggle).toBeChecked();
  });
  it("disables input and respects defaultChecked", async () => {
    const user = userEvent.setup();
    render(<Switch label="Readonly" defaultChecked disabled />);
    const toggle = screen.getByRole("switch", { name: "Readonly" });
    expect(toggle).toBeDisabled();
    await user.click(toggle);
    expect(toggle).toBeChecked();
  });
});
