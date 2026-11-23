import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InlineAlert } from "./InlineAlert";

describe("InlineAlert", () => {
  it.each(["info", "success", "warning", "error"] as const)("renders the %s variant", (variant) => {
    const { unmount } = render(<InlineAlert variant={variant} title="Update" body="Changes saved." />);
    const region = screen.getByRole(variant === "error" ? "alert" : "status");
    expect(region).toHaveTextContent("Update");
    expect(region).toHaveTextContent("Changes saved.");
    expect(region.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    unmount();
  });

  it("makes its action keyboard-accessible", () => {
    const action = vi.fn();
    render(<InlineAlert title="Retry" body="Couldn't save. Try again."
      action={{ label: "Try again", onClick: action }} />);
    const button = screen.getByRole("button", { name: "Try again" });
    fireEvent.click(button);
    expect(action).toHaveBeenCalledOnce();
  });
});
