import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProgressBar } from "./ProgressBar";

describe("ProgressBar", () => {
  it("exposes a named determinate value and visible fraction", () => {
    render(<ProgressBar label="Uploading" value={3} max={8} />);
    const bar = screen.getByRole("progressbar", { name: "Uploading" });
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(bar).toHaveAttribute("aria-valuemax", "8");
    expect(bar).toHaveAttribute("aria-valuetext", "3 of 8");
    expect(screen.getByText("3 of 8")).toBeVisible();
    expect(bar.firstElementChild).toHaveStyle({ width: "37.5%" });
  });

  it("supports indeterminate progress without inventing a value", () => {
    render(<ProgressBar label="Processing" indeterminate />);
    const bar = screen.getByRole("progressbar", { name: "Processing" });
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(bar).toHaveClass("gl-progress-track--indeterminate");
  });

  it("clamps values to the actual range", () => {
    render(<ProgressBar label="Complete" value={12} max={8} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "8");
  });
});
