import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it.each(["sm", "lg"] as const)("renders the %s icon, heading, explanation and action", (size) => {
    const action = vi.fn();
    const { unmount } = render(
      <EmptyState icon="search" title="No results" body="Try another search." size={size}
        actions={<Button variant="primary" onClick={action}>Try again</Button>} />,
    );
    expect(screen.getByRole("heading", { name: "No results" })).toBeVisible();
    expect(screen.getByText("Try another search.")).toBeVisible();
    expect(screen.getByRole("heading").parentElement).toHaveClass("gl-empty-state--" + size);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(action).toHaveBeenCalledOnce();
    unmount();
  });
});
