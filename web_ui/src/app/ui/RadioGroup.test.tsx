import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RadioGroup } from "./RadioGroup";

const options = [
  { value: "one", label: "First", description: "Runs locally" },
  { value: "two", label: "Second", description: "Uses the cloud", disabled: true },
  { value: "three", label: "Third" },
];

describe("RadioGroup", () => {
  it("names native radios and supports uncontrolled selection and arrow navigation", async () => {
    const user = userEvent.setup();
    render(<RadioGroup label="Provider" defaultValue="one" options={options} />);
    expect(screen.getByRole("radiogroup", { name: "Provider" })).toBeInTheDocument();
    const first = screen.getByRole("radio", { name: /First/ });
    const last = screen.getByRole("radio", { name: "Third" });
    expect(first).toBeChecked();
    first.focus();
    await user.keyboard("{ArrowDown}");
    expect(last).toBeChecked();
    expect(last).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(first).toBeChecked();
    expect(screen.getByRole("radio", { name: /Second/ })).toBeDisabled();
  });
  it("supports controlled card selection, descriptions, a leading bar and a check", async () => {
    const user = userEvent.setup(), onValueChange = vi.fn();
    const view = render(<RadioGroup label="Plan" variant="card" options={options} value="one" onValueChange={onValueChange} />);
    const first = screen.getByRole("radio", { name: /First/ });
    expect(first).toHaveAccessibleDescription("Runs locally");
    expect(first.closest(".gl-radio-card")).toHaveClass("gl-radio-selected");
    expect(first.closest(".gl-radio-card")?.querySelector(".gl-radio-check")).toBeTruthy();
    await user.click(screen.getByRole("radio", { name: "Third" }));
    expect(onValueChange).toHaveBeenCalledWith("three");
    expect(first).toBeChecked();
    view.rerender(<RadioGroup label="Plan" variant="card" options={options} value="three" onValueChange={onValueChange} />);
    expect(screen.getByRole("radio", { name: "Third" })).toBeChecked();
  });
  it("honours disabled and exposes group errors", () => {
    render(<RadioGroup label="Locked selection" options={options} disabled error="Not available" />);
    expect(screen.getByRole("radiogroup", { name: "Locked selection" })).toHaveAccessibleDescription("Not available");
    expect(screen.getByRole("radio", { name: /First/ })).toBeDisabled();
  });
  it("starts unselected when no default value is supplied", async () => {
    const user = userEvent.setup();
    render(<RadioGroup label="Choice" options={options} />);
    const first = screen.getByRole("radio", { name: /First/ });
    expect(first).not.toBeChecked();
    await user.click(first);
    expect(first).toBeChecked();
  });
});
