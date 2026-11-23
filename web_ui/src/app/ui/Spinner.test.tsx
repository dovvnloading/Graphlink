import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Spinner } from "./Spinner";

describe("Spinner", () => {
  it("announces its status and uses a labelled SVG arc at the default size", () => {
    const { container } = render(<Spinner />);
    expect(screen.getByRole("status", { name: "Loading" })).toHaveClass("gl-spinner--16");
    expect(container.querySelector(".gl-spinner-arc")).toHaveAttribute("viewBox", "0 0 20 20");
    expect(container.querySelector(".gl-spinner-arc circle")).toHaveAttribute("stroke-dasharray", "30 44");
  });

  it.each([12, 14, 16, 20] as const)("supports the %s px size", (size) => {
    render(<Spinner size={size} label="Running" />);
    expect(screen.getByRole("status", { name: "Running" })).toHaveClass("gl-spinner--" + size);
  });

  it("hides nested decorative statuses from assistive technology", () => {
    const { container } = render(<Spinner decorative />);
    expect(container.querySelector(".gl-spinner")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("provides a static status-running icon when reduced motion is requested", () => {
    const { container } = render(<Spinner />);
    expect(container.querySelector(".gl-spinner-static")).toBeInTheDocument();
    const css = readFileSync(join(process.cwd(), "src/app/ui/spinner.css"), "utf8");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("animation: gl-spinner-turn 0.8s linear infinite");
    expect(css).toContain(".gl-spinner-static { display: block; }");
  });
});
