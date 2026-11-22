import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Disclosure } from "./Disclosure";

describe("Disclosure", () => {
  it("uses a named button and linked region, toggled by keyboard", async () => {
    const user = userEvent.setup();
    render(<Disclosure title="Advanced options"><p>Detail content</p></Disclosure>);
    const trigger = screen.getByRole("button", { name: "Advanced options" });
    const region = document.getElementById(trigger.getAttribute("aria-controls") ?? "");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-controls", region?.id);
    expect(region).toHaveAttribute("role", "region");
    expect(region).toHaveAttribute("aria-labelledby", trigger.id);
    expect(region).not.toBeVisible();
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "Advanced options" })).toBe(region);
    expect(region).toBeVisible();
    await user.keyboard(" ");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(region).not.toBeVisible();
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "Advanced options" })).toBeVisible();
  });
  it("supports a controlled section variant and open callback", async () => {
    const user = userEvent.setup(), onOpenChange = vi.fn();
    const view = render(<Disclosure title="Details" variant="section" open={false} onOpenChange={onOpenChange}>Panel</Disclosure>);
    const trigger = screen.getByRole("button", { name: "Details" });
    expect(trigger.closest(".gl-disclosure-section")).toBeTruthy();
    await user.click(trigger);
    expect(onOpenChange).toHaveBeenCalledWith(true);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    view.rerender(<Disclosure title="Details" variant="section" open onOpenChange={onOpenChange}>Panel</Disclosure>);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "Details" })).toBeVisible();
  });
  it("defaults open and respects disabled", async () => {
    const user = userEvent.setup();
    render(<Disclosure title="Locked" defaultOpen disabled><span>Visible</span></Disclosure>);
    const trigger = screen.getByRole("button", { name: "Locked" });
    expect(trigger).toBeDisabled();
    expect(screen.getByText("Visible")).toBeVisible();
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });
});
