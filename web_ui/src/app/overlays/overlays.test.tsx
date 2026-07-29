import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Dialog, OverlayProvider, Popover, useOverlays } from "./overlays";

function Chrome() {
  const overlays = useOverlays();
  const [renameValue, setRenameValue] = useState("");
  const [renameCancelled, setRenameCancelled] = useState(false);
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
      <Popover name="view" label="View">
        <p>view popover body</p>
        <button type="button">first control</button>
      </Popover>
      <Dialog name="settings" title="Settings">
        {/* R8a finding #16: mirrors ChatLibraryDialog's own rename input -
            claims Escape via preventDefault() rather than letting it bubble
            to overlays.tsx's document-level handler. */}
        <input
          aria-label="rename field"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              setRenameCancelled(true);
            }
          }}
        />
        {renameCancelled && <span>rename cancelled</span>}
        <input aria-label="first field" />
        <button type="button">save</button>
        {/* R8a finding #17: a primary action disabled in its default state -
            mirrors Settings' own IntegrationsPage/ApiProviderPage/OllamaPage
            "Save" buttons, each gated on an empty draft field. Trailing the
            real "save" button so it - not this one - must be where Tab
            wraps back to "first field". */}
        <button type="button" disabled>
          disabled action
        </button>
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

  it("R8a finding #19: opening a popover moves focus onto its first focusable control", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^View/ }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "first control" }));
  });

  it("R8a finding #19: closing a popover restores focus to its trigger, same as a dialog", async () => {
    const user = setup();
    const trigger = screen.getByRole("button", { name: /^View/ });
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(trigger);
  });

  it("R8a finding #19: a popover has a real aria-label, not an unnamed dialog role", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^View/ }));
    expect(screen.getByRole("dialog", { name: "View" })).toBeInTheDocument();
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

  it("R8a finding #17: Tab from the real last-focusable wraps to the first, skipping a disabled trailing button", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^Settings/ }));
    // "disabled action" trails "save" in DOM order - if the trap still
    // counted it as focusable, activeElement === lastEl would never be
    // true for the real last element (save), the wrap branch would never
    // fire, and Tab would leak focus onto whatever's next in the whole
    // document outside the panel.
    screen.getByRole("button", { name: "save" }).focus();
    await user.keyboard("{Tab}");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close Settings" }));
  });

  it("R8a finding #17: a focus escape past the trap (Tab-driven) is pulled back into the panel", async () => {
    // Simulates a leak the trap's own boundary computation missed by some
    // OTHER cause than the disabled-button one already fixed above - the
    // belt-and-suspenders backstop must not depend on knowing why the
    // escape happened, only that a Tab press left focus outside the panel.
    //
    // fireEvent.keyDown(document, ...), not user.keyboard("{Tab}") - the
    // panel's own primary trap is now correct, so a realistic simulated Tab
    // would be caught (and consume the backstop's own "was Tab" flag) before
    // ever reaching this scenario. Dispatching the keydown directly at
    // document reaches this component's document-level capture listener
    // (arming the flag) WITHOUT reaching panel's own bubble-phase handler
    // (out of the event's path when document itself is the target) - i.e.
    // exactly "Tab was pressed, but the primary trap did not handle it",
    // the counterfactual this backstop exists for.
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^Settings/ }));
    const outsideButton = screen.getByRole("button", { name: "elsewhere" });

    fireEvent.keyDown(document, { key: "Tab" });
    outsideButton.focus(); // what a missed trap boundary would let happen

    expect(screen.getByRole("dialog", { name: "Settings" }).contains(document.activeElement)).toBe(
      true,
    );
  });

  it("R8a finding #17: closing a dialog restores focus to the opener even right after a Tab press (no fight with the backstop)", async () => {
    // The backstop above must not intercept close()'s own legitimate
    // focus-restoration just because the last key happened to be Tab.
    // Starts from "first field" (not the dialog's default initial focus,
    // the close button) specifically to land the Tab on "save" - a plain
    // button with no Escape handling of its own - rather than on "rename
    // field", which correctly (finding #16) claims Escape and would leave
    // the dialog open, an entirely different, already-covered scenario.
    const user = setup();
    const opener = screen.getByRole("button", { name: /^Settings/ });
    await user.click(opener);
    await user.click(screen.getByLabelText("first field"));
    await user.keyboard("{Tab}");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "save" }));
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(opener);
  });

  it("R8a finding #17: closing via a MOUSE click right after a Tab keydown does not fight the backstop either", async () => {
    // The scenario the pointerdown reset specifically exists for: a Tab
    // keydown with no OTHER intervening key (which would otherwise clear
    // the "was Tab" flag on its own, per onKeyDownCapture's own logic -
    // see the Escape case above), immediately followed by a mouse click
    // on the dialog's own close button, not a keypress. fireEvent, not
    // user.keyboard, for the same reason as the escape-past-the-trap test
    // above: a realistic Tab is handled correctly now and would consume
    // the flag itself, defeating the point of testing the reset.
    const user = setup();
    const opener = screen.getByRole("button", { name: /^Settings/ });
    await user.click(opener);

    fireEvent.keyDown(document, { key: "Tab" });
    await user.click(screen.getByRole("button", { name: "Close Settings" }));

    expect(document.activeElement).toBe(opener);
  });

  it("R8a finding #16: Escape does not close the dialog when an inner field claims it first", async () => {
    const user = setup();
    await user.click(screen.getByRole("button", { name: /^Settings/ }));
    await user.click(screen.getByLabelText("rename field"));
    await user.keyboard("{Escape}");

    // The field's own handler ran (preventDefault claimed it)...
    expect(screen.getByText("rename cancelled")).toBeInTheDocument();
    // ...and the dialog is deliberately still open, unlike the existing
    // "Escape closes whatever is open" test above (a field with no
    // preventDefault of its own, "first field" - the ordinary case, which
    // must still work exactly as before).
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeInTheDocument();
  });

  it("closing restores focus to the opener", async () => {
    const user = setup();
    const opener = screen.getByRole("button", { name: /^Settings/ });
    await user.click(opener);
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(opener);
  });
});
