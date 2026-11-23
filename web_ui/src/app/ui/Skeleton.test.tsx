import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Skeleton } from "./Skeleton";

describe("Skeleton", () => {
  it("renders decorative lines without extra announcements", () => {
    const { container } = render(<Skeleton lines={3} />);
    expect(container.querySelectorAll(".gl-skeleton-shape--line")).toHaveLength(3);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });
  it("renders a named block for a loading region", () => {
    render(<Skeleton variant="block" label="Loading preview" width="60%" />);
    const region = screen.getByRole("status", { name: "Loading preview" });
    expect(region.querySelector(".gl-skeleton-shape--block")).toHaveStyle({ width: "60%" });
  });
});
