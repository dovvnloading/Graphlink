import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FullWorkflowContents } from "./WorkflowContents";

it("does not request or mount content until Show full, and clears it on Hide full", async () => {
  const user = userEvent.setup();
  const load = vi.fn().mockResolvedValue({ input: "private prompt", output: "private output" });
  render(<FullWorkflowContents load={load} />);
  expect(load).not.toHaveBeenCalled();
  expect(screen.queryByText("private prompt")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Show full" }));
  expect(await screen.findByText("private prompt")).toBeVisible();
  expect(screen.getByText("private output")).toBeVisible();
  expect(load).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Hide full" }));
  expect(screen.queryByText("private output")).toBeNull();
});
