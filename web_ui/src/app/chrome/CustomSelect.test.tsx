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

    expect(screen.queryByRole("listbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    const panel = screen.getByRole("listbox", { name: "Pick one" });
    expect(panel).toBeInTheDocument();
    for (const option of OPTIONS) {
      // A description remains part of a listbox option's accessible name.
      expect(screen.getByRole("option", { name: new RegExp("^" + option.label) })).toBeInTheDocument();
    }
  });

  it("renders a portaled panel as a direct child of document.body, not nested under the trigger", async () => {
    const user = userEvent.setup();
    const { container } = render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    const panel = screen.getByRole("listbox", { name: "Pick one" });
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
    expect(screen.getByRole("option", { name: /^Option B/ })).toHaveClass("is-active");
    expect(screen.getByRole("option", { name: "Option A" })).not.toHaveClass("is-active");
    expect(screen.getByRole("option", { name: "Option C" })).not.toHaveClass("is-active");
  });

  it("clicking an option calls onChange with its id and closes the panel", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    await user.click(screen.getByRole("option", { name: "Option C" }));

    expect(onChange).toHaveBeenCalledWith("c");
    expect(screen.queryByRole("listbox")).toBeNull();
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
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Elsewhere" }));

    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("Escape closes the panel and calls event.preventDefault(), so a parent's own Escape-closes-everything listener (overlays.tsx's own contract) does not also fire", async () => {
    const user = userEvent.setup();
    const parentEscapeHandler = vi.fn();
    // Mirrors OverlayProvider's bubble-phase, defaultPrevented-gated Escape
    // listener: the focused listbox handles this Escape, not its host dialog.
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !event.defaultPrevented) parentEscapeHandler();
    });

    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("listbox")).toBeNull();
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
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("sets aria-haspopup and aria-expanded reflecting the panel's real open state", async () => {
    const user = userEvent.setup();
    render(<CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Pick one" });

    expect(trigger).toHaveAttribute("aria-haspopup", "listbox");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  it("opens with ArrowDown, focuses the listbox at the next option, and supports Home/End/Enter", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<CustomSelect ariaLabel="Pick one" value="b" options={OPTIONS} onChange={onChange} />);
    const trigger = screen.getByRole("button", { name: "Pick one" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    const listbox = screen.getByRole("listbox", { name: "Pick one" });
    expect(listbox).toHaveFocus();
    // ArrowDown starts on the next enabled option after the selected value.
    expect(listbox).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Option C" }).id);
    await user.keyboard("{Home}");
    expect(listbox).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Option A" }).id);
    await user.keyboard("{End}{Enter}");
    expect(onChange).toHaveBeenCalledWith("c");
    expect(screen.queryByRole("listbox", { name: "Pick one" })).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("Tab and Shift+Tab leave the portaled menu at the next or previous control", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button type="button">Previous</button>
        <CustomSelect ariaLabel="Pick one" value="a" options={OPTIONS} onChange={vi.fn()} />
        <button type="button">Next</button>
      </div>,
    );
    const trigger = screen.getByRole("button", { name: "Pick one" });
    await user.click(trigger);
    expect(screen.getByRole("listbox", { name: "Pick one" })).toHaveFocus();
    await user.keyboard("{Tab}");
    expect(screen.queryByRole("listbox", { name: "Pick one" })).toBeNull();
    expect(screen.getByRole("button", { name: "Next" })).toHaveFocus();
    await user.click(trigger);
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(screen.getByRole("button", { name: "Previous" })).toHaveFocus();
  });

  it("exposes its selected value and the chosen option to assistive technology", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CustomSelect ariaLabel="Pick one" value="b" options={OPTIONS} onChange={vi.fn()} />,
    );
    const trigger = screen.getByRole("button", { name: "Pick one" });
    expect(trigger).toHaveTextContent("Option B");
    expect(trigger).not.toHaveAccessibleDescription(/Selected:/);
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-controls", screen.getByRole("listbox").id);
    expect(screen.getByRole("option", { name: /^Option B/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: "Option A" })).toHaveAttribute("aria-selected", "false");
    await user.keyboard("{Escape}");
    rerender(<CustomSelect ariaLabel="Pick one" value="c" options={OPTIONS} onChange={vi.fn()} />);
    expect(trigger).toHaveTextContent("Option C");
    expect(trigger).not.toHaveAccessibleDescription(/Selected:/);
  });


});
