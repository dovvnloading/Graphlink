import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { IconButton } from "./IconButton";

describe("IconButton", () => {
  it.each(["ghost", "secondary", "primary"] as const)("renders the %s variant with a required accessible name", (variant) => {
    render(<IconButton icon="close" label="Close dialog" variant={variant} />);
    const button = screen.getByRole("button", { name: "Close dialog" });
    expect(button).toHaveClass("gl-icon-button--" + variant);
    expect(button).not.toHaveAttribute("title");
  });

  it.each(["sm", "md", "lg"] as const)("renders the %s control size", (size) => {
    const { container } = render(<IconButton icon="search" label="Search" size={size} />);
    expect(screen.getByRole("button")).toHaveClass("gl-icon-button--" + size);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("defaults to ghost and button type; stores shortcut for the Tooltip", () => {
    render(<IconButton icon="search" label="Search" shortcut="Ctrl+K" />);
    expect(screen.getByRole("button", { name: "Search" }))
      .toHaveClass("gl-icon-button--ghost");
    expect(screen.getByRole("button")).toHaveAttribute("aria-keyshortcuts", "Ctrl+K");
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });

  it("logs an error in development when the required label is empty", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(<IconButton icon="search" label="" />);
      expect(spy).toHaveBeenCalledWith("IconButton requires a non-empty label.");
    } finally {
      spy.mockRestore();
    }
  });

  it("activates by keyboard, but not while disabled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(<IconButton icon="search" label="Search" onClick={onClick} />);
    const button = screen.getByRole("button", { name: "Search" });
    button.focus();
    await user.keyboard(" ");
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(<IconButton icon="search" label="Search" disabled onClick={onClick} />);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
  it("shows label and Kbd shortcut in the tooltip on keyboard focus", () => {
    render(<IconButton icon="search" label="Search" shortcut="Ctrl+K" />);
    screen.getByRole("button", { name: "Search" }).focus();
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("Search");
    expect(tooltip.querySelector('[role="group"]')).toHaveAttribute("aria-label", "Ctrl+K");
    expect(screen.getByRole("button", { name: "Search" })).toHaveAttribute("aria-describedby", tooltip.id);
  });
});
