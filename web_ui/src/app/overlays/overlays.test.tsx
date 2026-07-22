import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Dialog, OverlayProvider, Popover, useOverlays } from "./overlays";

function Chrome() {
  const overlays = useOverlays();
  return (
    <div>
      <button
        type="button"
        data-overlay-trigger="view"
        onClick={() => overlays.toggle("view", "popover")}
      >
        View {overlays.isOpen("view") ? "(active)" : ""}
      </button>
      <button type="button" onClick={() => overlays.toggle("settings", "dialog")}>
        Settings {overlays.isOpen("settings") ? "(active)" : ""}
      </button>
      <button type="button">elsewhere</button>
      <Popover name="view">
        <p>view popover body</p>
      </Popover>
      <Dialog name="settings" title="Settings">
        <input aria-label="first field" />
        <button type="button">save</button>
      </Dialog>
    </div>
  );
}

function setup() {
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <Chrome />
    </OverlayProvider>,
  );
  return user;
}

describe("overlay system (the OverlayManager contract)", () => {
  it("toggle opens and closes a popover, chip state reflects REAL visibility", async () => {
    const user = setup();
    expect(screen.queryByText("view popover body")).toBeNull();
    await user.click(screen.getByRole("button", { name: /^View/ }));
    expect(screen.getByText("view popover body")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /View \(active\)/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /View \(active\)/ }));
    expect(screen.queryByText("view popover body")).toBeNull();
  });

  it("single-open across tiers: opening the dialog closes the popover", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^View/ }));
    expect(screen.getByText("view popover body")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Settings/ }));
    expect(screen.queryByText("view popover body")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeInTheDocument();
  });

  it("Escape closes whatever is open - including with focus in a dialog input", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^Settings/ }));
    await user.click(screen.getByLabelText("first field"));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull();
  });

  it("outside-click dismisses a popover but a click INSIDE does not", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^View/ }));
    await user.click(screen.getByText("view popover body"));
    expect(screen.getByText("view popover body")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(screen.queryByText("view popover body")).toBeNull();
  });

  it("every dialog has a working close button (audit B5)", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^Settings/ }));
    await user.click(screen.getByRole("button", { name: "Close Settings" }));
    expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull();
  });

  it("dialog focus lands inside on open and Tab cycles within the panel", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^Settings/ }));
    expect(screen.getByRole("dialog", { name: "Settings" }).contains(document.activeElement)).toBe(
      true,
    );
    // Tab from the last focusable wraps to the first (close button).
    screen.getByRole("button", { name: "save" }).focus();
    await user.keyboard("{Tab}");
    expect(screen.getByRole("dialog", { name: "Settings" }).contains(document.activeElement)).toBe(
      true,
    );
  });

  it("closing restores focus to the opener", async () => {
    const user = setup();
    const opener = screen.getByRole("button", { name: /^Settings/ });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(opener);
  });
});
