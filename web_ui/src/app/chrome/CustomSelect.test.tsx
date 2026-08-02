import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CustomSelect } from "./CustomSelect";

const OPTIONS = [
  { id: "a", label: "Option A" },
  { id: "b", label: "Option B", description: "The second choice" },
  { id: "c", label: "Option C" },
];

describe("CustomSelect", () => {
  it("shows the selected option's label on the trigger, not the placeholder", () => {
    render(<CustomSelect ariaLabel="Pick one" value="b" options={OPTIONS} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Pick one" })).toHaveTextContent("Option B");
  });

  it("falls back to the placeholder when the current value matches no option", () => {
    render(<CustomSelect ariaLabel="Pick one" value="nonexistent" options={OPTIONS} onChange={vi.fn()} placeholder="Choose…" />);
    expect(screen.getByRole("button", { name: "Pick one" })).toHaveTextContent("Choose…");
  });

  it("the panel is closed by default and opens on trigger click, listing every option", async () => {
    const user = userEvent.setup();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);

    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    const panel = screen.getByRole("dialog", { name: "Pick one" });
    expect(panel).toBeInTheDocument();
    for (const option of OPTIONS) {
      // A regex, not an exact string: an option WITH a description (like
      // Option B here) gets its description text folded into its own
      // accessible name too (both are plain text inside the same <button>,
      // no aria-hidden on either) - matching Composer.tsx's own Reasoning
      // picker exactly, which this component deliberately reuses verbatim.
      expect(screen.getByRole("button", { name: new RegExp("^" + option.label) })).toBeInTheDocument();
    }
  });

  it("renders a portaled panel as a direct child of document.body, not nested under the trigger", async () => {
    const user = userEvent.setup();
    const { container } = render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    const panel = screen.getByRole("dialog", { name: "Pick one" });
    expect(container.contains(panel)).toBe(false);
    expect(document.body.contains(panel)).toBe(true);
  });

  it("renders an option's description when given one", async () => {
    const user = userEvent.setup();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    expect(screen.getByText("The second choice")).toBeInTheDocument();
  });

  it("marks the currently-selected option with the active class, and no other option", async () => {
    const user = userEvent.setup();
    render(<CustomSelect ariaLabel="Pick one" value="b" options={OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    // Option B's own accessible name also folds in its description text -
    // see the "listing every option" test's own comment.
    expect(screen.getByRole("button", { name: /^Option B/ })).toHaveClass("active");
    expect(screen.getByRole("button", { name: "Option A" })).not.toHaveClass("active");
    expect(screen.getByRole("button", { name: "Option C" })).not.toHaveClass("active");
  });

  it("clicking an option calls onChange with its id and closes the panel", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    await user.click(screen.getByRole("button", { name: "Option C" }));

    expect(onChange).toHaveBeenCalledWith("c");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("clicking outside the panel closes it without calling onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <div>
        <CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={onChange} />
        <button type="button">Elsewhere</button>
      </div>,
    );
    await user.click(screen.getByRole("button", { name: "Pick one" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Elsewhere" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("Escape closes the panel and calls event.preventDefault(), so a parent's own Escape-closes-everything listener (overlays.tsx's own contract) does not also fire", async () => {
    const user = userEvent.setup();
    const parentEscapeHandler = vi.fn();
    // Mirrors overlays.tsx's own OverlayProvider Escape listener: bubble
    // phase, gated on event.defaultPrevented - the exact contract this
    // component's own Escape handler (capture phase, preventDefault) must
    // satisfy so opening a CustomSelect inside an already-open Settings
    // Dialog can close ITS OWN panel without also closing the dialog.
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !event.defaultPrevented) parentEscapeHandler();
    });

    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(parentEscapeHandler).not.toHaveBeenCalled();
  });

  it("Escape returns focus to the trigger button", async () => {
    const user = userEvent.setup();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Pick one" });
    await user.click(trigger);
    await user.keyboard("{Escape}");

    expect(trigger).toHaveFocus();
  });

  it("a disabled select cannot be opened", async () => {
    const user = userEvent.setup();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} disabled />);

    const trigger = screen.getByRole("button", { name: "Pick one" });
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("sets aria-haspopup and aria-expanded reflecting the panel's real open state", async () => {
    const user = userEvent.setup();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Pick one" });

    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });
});
