import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StalenessBadge } from "./StalenessBadge";

describe("StalenessBadge", () => {
  it("uses the kit's Out of date status pill and no title attribute", () => {
    const { container } = render(<StalenessBadge status="upstream_changed" />);
    expect(screen.getByRole("status", { name: "Out of date" })).toHaveTextContent("Out of date");
    expect(container.querySelector(".node-staleness-badge svg")).not.toBeNull();
    expect(container.querySelector("[title]")).toBeNull();
    expect(screen.getByText(/Upstream inputs have changed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run bound nodes" })).toBeNull();
  });

  it("offers a labelled, keyboard-operable bound-node handoff, never automatic execution", async () => {
    const user = userEvent.setup();
    const runBound = vi.fn();
    render(<StalenessBadge status="upstream_changed" onRunBoundNodes={runBound} />);
    const action = screen.getByRole("button", { name: "Run bound nodes" });
    action.focus();
    await user.keyboard("{Enter}");
    expect(runBound).toHaveBeenCalledOnce();
    await user.keyboard(" ");
    expect(runBound).toHaveBeenCalledTimes(2);
  });

  it("never badges a Thinking trace as a manual step", () => {
    const { rerender } = render(<StalenessBadge nodeKind="thinking" status="upstream_changed" />);
    expect(screen.queryByRole("status")).toBeNull();
    rerender(<StalenessBadge nodeKind="thinking" status="out_of_date" manualRerun />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keeps manual re-run explanation for outdated replies without a bound action", () => {
    render(<StalenessBadge status="out_of_date" manualRerun />);
    expect(screen.getByRole("status", { name: "Out of date" })).toBeInTheDocument();
    expect(screen.getByText(/re-run manually/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run bound nodes" })).toBeNull();
  });
});
