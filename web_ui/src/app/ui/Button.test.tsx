import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";

describe("Button", () => {
  it.each(["primary", "secondary", "ghost"] as const)("renders the %s variant with a button role and name", (variant) => {
    render(<Button variant={variant}>Save changes</Button>);
    const button = screen.getByRole("button", { name: "Save changes" });
    expect(button).toHaveClass("gl-button--" + variant);
    expect(button).toHaveAttribute("type", "button");
    expect(button).not.toHaveAttribute("title");
  });

  it.each(["sm", "md", "lg"] as const)("renders size %s", (size) => {
    render(<Button size={size}>Open library</Button>);
    expect(screen.getByRole("button", { name: "Open library" })).toHaveClass("gl-button--" + size);
  });

  it("uses a submit type only when explicitly requested", () => {
    render(<Button type="submit">Save changes</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "submit");
  });

  it("renders decorative start and end icons without changing the accessible name", () => {
    const { container } = render(<Button iconStart="download" iconEnd="chevron-right">Save changes</Button>);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeInTheDocument();
    expect(container.querySelectorAll(".gl-button-icon svg")).toHaveLength(2);
    expect(container.querySelectorAll(".gl-button-icon svg[aria-hidden='true']")).toHaveLength(2);
  });

  it("replaces the start icon with a hidden spinner, preserves the slot and marks busy", () => {
    const { container, rerender } = render(<Button iconStart="download">Download file</Button>);
    expect(container.querySelector(".gl-button-icon")).toBeInTheDocument();
    rerender(<Button iconStart="download" loading>Download file</Button>);
    const button = screen.getByRole("button", { name: "Download file" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    expect(container.querySelector(".gl-button-icon")).toBeInTheDocument();
    expect(container.querySelector(".gl-spinner")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(container.querySelector(".gl-button-icon .gl-spinner-arc")).toBeInTheDocument();
  });


  it("uses an overlay when loading without a leading icon, preserving the accessible label", () => {
    const { container, rerender } = render(<Button>Save changes</Button>);
    const button = screen.getByRole("button", { name: "Save changes" });
    expect(container.querySelector(".gl-button-loading-indicator")).not.toBeInTheDocument();
    rerender(<Button loading>Save changes</Button>);
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleName("Save changes");
    expect(container.querySelector(".gl-button-loading-indicator .gl-spinner")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector(".gl-button-label")).toHaveClass("gl-button-label--loading");
    expect(container.querySelector(".gl-button-icon")).not.toBeInTheDocument();
  });

  it("exposes the disabled reason through aria-describedby without a title tooltip", () => {
    render(<Button disabled disabledReason="Requires a workspace" aria-describedby="external">Save changes</Button>);
    const button = screen.getByRole("button", { name: "Save changes" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Requires a workspace");
    expect(button.getAttribute("aria-describedby")).toContain("external");
    expect(button).not.toHaveAttribute("title");
  });

  it("does not expose a reason for an enabled button", () => {
    render(<Button disabledReason="Not ready">Save changes</Button>);
    expect(screen.getByRole("button")).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByText("Not ready")).not.toBeInTheDocument();
  });

  it("uses the same neutral variant for destructive actions with a leading trash glyph", () => {
    const { container } = render(<Button destructive variant="secondary" iconStart="download">Delete chat</Button>);
    const button = screen.getByRole("button", { name: "Delete chat" });
    expect(button).toHaveAttribute("data-destructive", "true");
    expect(button).toHaveClass("gl-button--secondary");
    expect(container.querySelector(".gl-button-icon svg")).toBeInTheDocument();
    expect(button).not.toHaveClass("gl-button--primary");
  });

  it("can fill a row and responds to Enter, but disables all activation when disabled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(<Button fullWidth onClick={onClick}>Open library</Button>);
    const button = screen.getByRole("button", { name: "Open library" });
    expect(button).toHaveClass("gl-button--full-width");
    button.focus();
    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(<Button fullWidth disabled onClick={onClick}>Open library</Button>);
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
