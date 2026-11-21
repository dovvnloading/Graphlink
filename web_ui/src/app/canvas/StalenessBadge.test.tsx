import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StalenessBadge } from "./StalenessBadge";

describe("StalenessBadge", () => {
  it("uses an outlined warning shape and explicit text for manual re-run", () => {
    const { container } = render(<StalenessBadge status="upstream_changed" />);
    expect(screen.getByRole("status")).toHaveTextContent("Upstream changed - re-run manually");
    expect(container.querySelector(".node-staleness-badge svg")).not.toBeNull();
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

  it("never badges a Thinking trace as a manual step, even with legacy stale statuses", () => {
    const { rerender } = render(<StalenessBadge nodeKind="thinking" status="upstream_changed" />);
    expect(screen.queryByRole("status")).toBeNull();
    rerender(<StalenessBadge nodeKind="thinking" status="out_of_date" manualRerun />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("requires manual re-run for an outdated tool reply, without a bound action", () => {
    const { container } = render(<StalenessBadge status="out_of_date" manualRerun />);
    expect(screen.getByRole("status")).toHaveTextContent("Upstream changed - re-run manually");
    expect(container.querySelector(".node-staleness-badge svg")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Run bound nodes" })).toBeNull();
  });
});
