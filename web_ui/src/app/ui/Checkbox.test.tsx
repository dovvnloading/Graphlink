import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Checkbox } from "./Checkbox";

describe("Checkbox", () => {
  it("uses a named native input and toggles in uncontrolled use with space", async () => {
    const user = userEvent.setup();
    render(<Checkbox label="Save history" description="Stored locally" />);
    const control = screen.getByRole("checkbox", { name: "Save history" });
    expect(control).not.toBeChecked();
    expect(control).toHaveAttribute("aria-describedby", expect.stringContaining("description"));
    control.focus();
    await user.keyboard(" ");
    expect(control).toBeChecked();
    await user.click(screen.getByText("Save history"));
    expect(control).not.toBeChecked();
  });
  it("supports controlled checked and error display", async () => {
    const user = userEvent.setup(), onChange = vi.fn();
    const view = render(<Checkbox label="Sync" checked={false} onChange={onChange} error="Unavailable" />);
    const control = screen.getByRole("checkbox", { name: "Sync" });
    expect(control).toHaveAttribute("aria-invalid", "true");
    expect(control).toHaveAccessibleDescription("Unavailable");
    await user.click(control);
    expect(onChange).toHaveBeenCalledOnce();
    expect(control).not.toBeChecked();
    view.rerender(<Checkbox label="Sync" checked onChange={onChange} />);
    expect(control).toBeChecked();
  });
  it("sets the indeterminate DOM state and mixed ARIA state", () => {
    const view = render(<Checkbox label="Select all" indeterminate />);
    const control = screen.getByRole("checkbox", { name: "Select all" }) as HTMLInputElement;
    expect(control.indeterminate).toBe(true);
    expect(control).toHaveAttribute("aria-checked", "mixed");
    view.rerender(<Checkbox label="Select all" defaultChecked />);
    expect(control.indeterminate).toBe(false);
    expect(control).toBeChecked();
  });
  it("honours disabled and defaultChecked", async () => {
    const user = userEvent.setup();
    render(<Checkbox label="Offline" defaultChecked disabled />);
    const control = screen.getByRole("checkbox", { name: "Offline" });
    expect(control).toBeDisabled();
    expect(control).toBeChecked();
    await user.click(control);
    expect(control).toBeChecked();
  });
});
