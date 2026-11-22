import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NumberField } from "./NumberField";

describe("NumberField", () => {
  it("provides a named native spinbutton and labeled steppers", async () => {
    const user = userEvent.setup();
    render(<NumberField label="Retries" defaultValue={2} min={0} max={3} unit="times" />);
    const spin = screen.getByRole("spinbutton", { name: "Retries" });
    expect(spin).toHaveValue(2);
    expect(screen.getByText("times")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Increase Retries" }));
    expect(spin).toHaveValue(3);
    expect(screen.getByRole("button", { name: "Increase Retries" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Decrease Retries" }));
    expect(spin).toHaveValue(2);
  });
  it("supports arrow and page stepping, clamping and fractional increments", async () => {
    const user = userEvent.setup();
    render(<NumberField label="Opacity" defaultValue={0.3} min={0} max={1} step={0.1} />);
    const spin = screen.getByRole("spinbutton", { name: "Opacity" });
    spin.focus();
    await user.keyboard("{ArrowUp}");
    expect(spin).toHaveValue(0.4);
    await user.keyboard("{ArrowDown}");
    expect(spin).toHaveValue(0.3);
    await user.keyboard("{PageUp}");
    expect(spin).toHaveValue(1);
    await user.keyboard("{PageDown}");
    expect(spin).toHaveValue(0);
  });
  it("clamps typed values on blur, including empty input", () => {
    render(<NumberField label="Count" min={1} max={10} />);
    const spin = screen.getByRole("spinbutton", { name: "Count" });
    fireEvent.change(spin, { target: { value: "99" } });
    fireEvent.blur(spin);
    expect(spin).toHaveValue(10);
    fireEvent.change(spin, { target: { value: "" } });
    fireEvent.blur(spin);
    expect(spin).toHaveValue(null);
  });
  it("supports controlled changes, error, and disabled state", async () => {
    const user = userEvent.setup(), onValueChange = vi.fn();
    const view = render(<NumberField label="Pages" value={2} min={0} max={5} onValueChange={onValueChange} error="Choose fewer" />);
    const spin = screen.getByRole("spinbutton", { name: "Pages" });
    expect(spin).toHaveAttribute("aria-invalid", "true");
    expect(spin).toHaveAccessibleDescription("Choose fewer");
    await user.click(screen.getByRole("button", { name: "Increase Pages" }));
    expect(onValueChange).toHaveBeenCalledWith(3);
    expect(spin).toHaveValue(2);
    view.rerender(<NumberField label="Pages" value={3} min={0} max={5} disabled />);
    expect(spin).toBeDisabled();
    expect(screen.getByRole("button", { name: "Increase Pages" })).toBeDisabled();
  });
});
