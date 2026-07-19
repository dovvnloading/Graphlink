import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComposerApp from "./ComposerApp";

// jsdom has no window.QWebChannel, so createComposerBridge() (in bridge.ts)
// falls through to MockComposerBridge automatically - this is the "vitest
// green with a mock-bridge composer test" Phase 1 exit criterion, exercised
// through ComposerApp's real public behavior rather than reaching into
// MockComposerBridge internals directly.

describe("ComposerApp against the mock bridge", () => {
  it("renders the message input and starts with the send button disabled", () => {
    render(<ComposerApp />);
    expect(screen.getByLabelText("Message composer")).toBeInTheDocument();
    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("enables send once the draft has non-whitespace text", async () => {
    const user = userEvent.setup();
    render(<ComposerApp />);

    const input = screen.getByLabelText("Message composer");
    await user.type(input, "Hello there");

    expect(screen.getByLabelText("Send message")).toBeEnabled();
  });

  it("keeps send disabled for whitespace-only text", async () => {
    const user = userEvent.setup();
    render(<ComposerApp />);

    const input = screen.getByLabelText("Message composer");
    await user.type(input, "   ");

    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("sending transitions into the mock bridge's preview-generating state", async () => {
    const user = userEvent.setup();
    render(<ComposerApp />);

    await user.type(screen.getByLabelText("Message composer"), "Hello there");
    await user.click(screen.getByLabelText("Send message"));

    expect(await screen.findByText("Preview mode — waiting for the desktop bridge")).toBeInTheDocument();
    expect(screen.getByLabelText("Cancel response")).toBeInTheDocument();
  });

  it("Enter sends when sendMode is enter_to_send (the mock bridge's default)", async () => {
    const user = userEvent.setup();
    render(<ComposerApp />);

    const input = screen.getByLabelText("Message composer");
    await user.type(input, "Hello there{Enter}");

    expect(await screen.findByText("Preview mode — waiting for the desktop bridge")).toBeInTheDocument();
  });

  it("large pastes are staged as a context attachment instead of inserted as text", async () => {
    const user = userEvent.setup();
    render(<ComposerApp />);

    const input = screen.getByLabelText("Message composer") as HTMLTextAreaElement;
    const bigText = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");

    await user.click(input);
    await user.paste(bigText);

    expect(input.value).toBe("");
    expect(await screen.findByLabelText(/Review 1 attached item/)).toBeInTheDocument();
  });

  it("attach button is enabled per the mock bridge's initial capabilities", () => {
    render(<ComposerApp />);
    expect(screen.getByLabelText("Attach context")).toBeEnabled();
  });
});
