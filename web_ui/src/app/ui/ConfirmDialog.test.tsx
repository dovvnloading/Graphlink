import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Dialog, OverlayProvider, useOverlays } from "../overlays/overlays";
import { Button } from "./Button";
import { ConfirmDialog, useConfirm } from "./ConfirmDialog";

function Harness() {
  const overlays = useOverlays();
  const confirm = useConfirm();
  const [answer, setAnswer] = useState("Pending");
  return (
    <>
      <Button onClick={() => overlays.open("parent-settings", "dialog")}>Open settings</Button>
      <Dialog name="parent-settings" title="Settings">
        <Button onClick={() => {
          void confirm({
            title: "Delete note?", body: "This cannot be undone.", confirmLabel: "Delete note",
            destructive: true, billing: { estimate: "$1.50" }, detail: "The note will be removed.",
          }).then((accepted) => setAnswer(accepted ? "Accepted" : "Cancelled"));
        }}>Delete note</Button>
      </Dialog>
      <ConfirmDialog />
      <output aria-label="Confirmation result">{answer}</output>
    </>
  );
}

describe("ConfirmDialog and useConfirm", () => {
  it("opens nested above an existing dialog, focuses Cancel, and Escape closes only the confirm", async () => {
    const user = userEvent.setup();
    render(<OverlayProvider><Harness /></OverlayProvider>);
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.click(screen.getByRole("button", { name: "Delete note" }));
    const nested = await screen.findByRole("dialog", { name: "Delete note?" });
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    expect(nested.closest("[data-nested]")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Delete note", exact: true })).toHaveAttribute("data-destructive", "true");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.getByRole("status", { name: "Confirmation result" })).toHaveTextContent("Cancelled"));
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Delete note?" })).toBeNull();
  });

  it("resolves true for the primary verb and shows billing and optional details", async () => {
    const user = userEvent.setup();
    render(<OverlayProvider><Harness /></OverlayProvider>);
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.click(screen.getByRole("button", { name: "Delete note" }));
    expect(await screen.findByText("Estimated cost: $1.50")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("The note will be removed.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Delete note", exact: true }));
    await waitFor(() => expect(screen.getByRole("status", { name: "Confirmation result" })).toHaveTextContent("Accepted"));
  });

  it("resolves false when Cancel is activated by keyboard", async () => {
    const user = userEvent.setup();
    render(<OverlayProvider><Harness /></OverlayProvider>);
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.click(screen.getByRole("button", { name: "Delete note" }));
    expect(await screen.findByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("status", { name: "Confirmation result" })).toHaveTextContent("Cancelled"));
  });
});
