import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Slider } from "./Slider";

describe("Slider", () => {
  it("uses a named native range input with optional tick and value labels", () => {
    render(<Slider label="Volume" defaultValue={40} showValue ticks={[{value: 0, label: "Low"}, {value: 100, label: "High"}]} />);
    const slider = screen.getByRole("slider", { name: "Volume" });
    expect(slider).toHaveValue("40");
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("Low")).toBeInTheDocument();
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(slider.closest(".gl-slider-rail")?.querySelector(".gl-slider-fill")).toHaveStyle({ width: "40%" });
  });
  it("supports native keyboard stepping and controlled state", async () => {
    const user = userEvent.setup(), onValueChange = vi.fn();
    const view = render(<Slider label="Zoom" min={0} max={10} step={2} value={4} onValueChange={onValueChange} />);
    const slider = screen.getByRole("slider", { name: "Zoom" });
    slider.focus();
    await user.keyboard("{ArrowRight}");
    expect(onValueChange).toHaveBeenCalledWith(6);
    expect(slider).toHaveValue("4");
    view.rerender(<Slider label="Zoom" min={0} max={10} step={2} value={6} onValueChange={onValueChange} />);
    expect(slider).toHaveValue("6");
  });
  it("updates uncontrolled value, respects errors and disabled, and supports formatted output", () => {
    const view = render(<Slider label="Threshold" defaultValue={25} description="Percentage" error="Too low"
      showValue formatValue={(value) => value + "%"} />);
    const slider = screen.getByRole("slider", { name: "Threshold" });
    expect(slider).toHaveAttribute("aria-invalid", "true");
    expect(slider).toHaveAccessibleDescription("Percentage Too low");
    fireEvent.change(slider, { target: { value: "50" } });
    expect(slider).toHaveValue("50");
    expect(screen.getByText("50%")).toBeInTheDocument();
    view.rerender(<Slider label="Threshold" defaultValue={25} disabled />);
    expect(slider).toBeDisabled();
  });
});
