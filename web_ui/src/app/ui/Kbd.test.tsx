import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Kbd } from "./Kbd";

describe("Kbd", () => {
  it("renders one accessible shortcut as separate 11px key caps", () => {
    const { container } = render(<Kbd shortcut="Ctrl+Shift+K" />);
    expect(screen.getByRole("group", { name: "Ctrl+Shift+K" })).toBeInTheDocument();
    const caps = container.querySelectorAll("kbd");
    expect(caps).toHaveLength(3);
    expect(Array.from(caps, (cap) => cap.textContent)).toEqual(["Ctrl", "Shift", "K"]);
    caps.forEach((cap) => expect(cap).toHaveClass("gl-kbd-key"));
  });

  it("supports a single key and trims whitespace around a shortcut", () => {
    const { container, rerender } = render(<Kbd shortcut="K" />);
    expect(container.querySelectorAll("kbd")).toHaveLength(1);
    rerender(<Kbd shortcut="Ctrl + K" />);
    expect(Array.from(container.querySelectorAll("kbd"), (cap) => cap.textContent)).toEqual(["Ctrl", "K"]);
  });

  it("does not create a focusable or interactive element", () => {
    const { container } = render(<Kbd shortcut="Ctrl+K" />);
    expect(container.querySelectorAll("button, input, [tabindex]")).toHaveLength(0);
  });
});
