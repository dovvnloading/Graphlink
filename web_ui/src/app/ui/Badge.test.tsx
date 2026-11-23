import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge, StatusPill } from "./Badge";

describe("Badge and StatusPill", () => {
  it("shows neutral badges in both text sizes", () => {
    const { rerender } = render(<Badge>12</Badge>);
    expect(screen.getByText("12")).toHaveClass("gl-badge--2xs");
    rerender(<Badge size="xs">Queued</Badge>);
    expect(screen.getByText("Queued")).toHaveClass("gl-badge--xs");
  });

  it.each(["Running", "Done", "Failed", "Cancelled", "Waiting for approval", "Out of date", "Skipped"] as const)(
    "labels the %s status without relying on color", (status) => {
      const { unmount } = render(<StatusPill status={status} />);
      const pill = screen.getByRole("status", { name: status });
      expect(pill).toHaveTextContent(status);
      expect(pill.querySelector("svg")).toBeInTheDocument();
      if (status === "Running") expect(pill.querySelector(".gl-spinner")).toBeInTheDocument();
      unmount();
    },
  );
});
