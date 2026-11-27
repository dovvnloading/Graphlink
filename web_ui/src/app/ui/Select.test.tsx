import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Select, type SelectOption } from "./Select";

const OPTIONS: SelectOption[] = [
  { value: "a", label: "Alpha", group: "Letters" },
  { value: "b", label: "Beta", description: "Second choice", group: "Letters" },
  { value: "c", label: "Charlie", group: "Letters", disabled: true },
  { value: "d", label: "Delta", group: "More" },
  { value: "e", label: "Echo", group: "More" },
];

function setup(options: SelectOption[] = OPTIONS, value = "b") {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<Select label="Delivery option" value={value} options={options} onChange={onChange} />);
  return { user, onChange, trigger: screen.getByRole("button", { name: "Delivery option" }) };
}

describe("kit Select", () => {
  it("exposes a labelled listbox trigger without a selected-value echo", async () => {
    const { user, trigger } = setup();
    expect(trigger).toHaveAttribute("aria-haspopup", "listbox");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveTextContent("Beta");
    expect(screen.queryByText(/Selected: Beta/)).toBeNull();
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", screen.getByRole("listbox").id);
    expect(screen.getByRole("option", { name: /Beta/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Second choice")).toBeInTheDocument();
    expect(screen.getByText("Letters")).toBeInTheDocument();
  });

  it("navigates arrows, Home/End and Enter, skipping disabled options", async () => {
    const { user, onChange, trigger } = setup();
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("listbox")).toHaveFocus();
    expect(screen.getByRole("listbox")).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Delta" }).id);
    await user.keyboard("{Home}");
    expect(screen.getByRole("listbox")).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Alpha" }).id);
    await user.keyboard("{End}{ArrowDown}{ArrowUp}{Enter}");
    expect(onChange).toHaveBeenCalledWith("e");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("keeps the keyboard target when a stationary pointer enters an opening panel", async () => {
    const { user, onChange, trigger } = setup();
    trigger.focus();
    await user.keyboard("{Home}");
    const listbox = screen.getByRole("listbox");
    const first = screen.getByRole("option", { name: "Alpha" });
    const selected = screen.getByRole("option", { name: /Beta/ });
    expect(listbox).toHaveAttribute("aria-activedescendant", first.id);
    // Chromium can synthesize mouseenter when layout places a menu under
    // a stationary pointer, even though the user is navigating by keyboard.
    fireEvent.mouseEnter(selected);
    expect(listbox).toHaveAttribute("aria-activedescendant", first.id);
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("a");
  });

  it("still moves the active option on real pointer movement", async () => {
    const { user, onChange, trigger } = setup();
    trigger.focus();
    await user.keyboard("{Home}");
    const delta = screen.getByRole("option", { name: "Delta" });
    fireEvent.pointerMove(delta, { pointerType: "mouse", clientX: 10, clientY: 20 });
    expect(screen.getByRole("listbox")).toHaveAttribute("aria-activedescendant", delta.id);
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("d");
  });

  it("supports incremental type-ahead by option label", async () => {
    const { user, trigger, onChange } = setup();
    await user.click(trigger);
    await user.keyboard("d");
    expect(screen.getByRole("listbox")).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Delta" }).id);
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("d");
  });

  it("Escape closes the listbox only and restores focus to its trigger", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("light dismisses on outside pointerdown and does not choose a value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<div>
      <Select label="Choose" value="a" options={OPTIONS} onChange={onChange} />
      <button type="button">Other control</button>
    </div>);
    await user.click(screen.getByRole("button", { name: "Choose" }));
    await user.click(screen.getByRole("button", { name: "Other control" }));
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows a searchable combobox when there are more than eight options", async () => {
    const options = Array.from({ length: 10 }, (_, index) => ({
      value: String(index), label: `Option ${index}`,
    }));
    const { user, onChange, trigger } = setup(options, "0");
    await user.click(trigger);
    const search = screen.getByRole("combobox", { name: "Search Delivery option" });
    expect(search).toHaveFocus();
    await user.type(search, "Option 9");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(search).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Option 9" }).id);
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("9");
  });

  it("search text keeps native caret keys: Home/End move the caret, not the option (#1145)", async () => {
    const options = Array.from({ length: 10 }, (_, index) => ({ value: String(index), label: `Option ${index}` }));
    const { user, trigger } = setup(options, "0");
    await user.click(trigger);
    const search = screen.getByRole("combobox", { name: "Search Delivery option" }) as HTMLInputElement;
    await user.type(search, "tion 1");
    const activeBefore = search.getAttribute("aria-activedescendant");
    await user.keyboard("{Home}Op");
    expect(search).toHaveValue("Option 1");
    expect(search).toHaveFocus();
    expect(search.getAttribute("aria-activedescendant")).toBe(activeBefore);
    await user.keyboard("{End}");
    expect(search.selectionStart).toBe("Option 1".length);
  });

  it("a composing Enter does not choose an option; the next Enter does (#1145)", async () => {
    const options = Array.from({ length: 10 }, (_, index) => ({ value: String(index), label: `Option ${index}` }));
    const { onChange, trigger, user } = setup(options, "0");
    await user.click(trigger);
    const search = screen.getByRole("combobox", { name: "Search Delivery option" });
    fireEvent.keyDown(search, { key: "Enter", isComposing: true });
    fireEvent.keyDown(search, { key: "Enter", keyCode: 229 });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.keyDown(search, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("0");
  });

  it("an open panel follows its trigger on ancestor scroll and window resize (#1146)", async () => {
    const { user, trigger } = setup();
    let rect = { top: 100, bottom: 130, left: 40, right: 240, width: 200, height: 30, x: 40, y: 100 };
    trigger.getBoundingClientRect = () => ({ ...rect, toJSON: () => rect }) as DOMRect;
    await user.click(trigger);
    const panel = screen.getByRole("listbox").parentElement as HTMLElement;
    expect(panel.style.top).toBe("136px");
    expect(panel.style.left).toBe("40px");

    rect = { ...rect, top: 300, bottom: 330, left: 90, x: 90, y: 300 };
    fireEvent.scroll(document.body);
    expect(panel.style.top).toBe("336px");
    expect(panel.style.left).toBe("90px");

    rect = { ...rect, top: 50, bottom: 80, y: 50 };
    fireEvent(window, new Event("resize"));
    expect(panel.style.top).toBe("86px");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("caps the panel height to the space available so every option stays reachable (#1146)", async () => {
    const { user, trigger } = setup();
    const rect = { top: 600, bottom: 630, left: 40, right: 240, width: 200, height: 30, x: 40, y: 600 };
    trigger.getBoundingClientRect = () => ({ ...rect, toJSON: () => rect }) as DOMRect;
    await user.click(trigger);
    const panel = screen.getByRole("listbox").parentElement as HTMLElement;
    // jsdom's 768px window leaves 124px below this trigger.
    expect(panel.style.getPropertyValue("--gl-select-available-height")).toBe(`${window.innerHeight - 630 - 6 - 8}px`);
  });

  it("does not render a search field for eight or fewer options", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("does not open or select disabled options", async () => {
    const user = userEvent.setup();
    render(<Select label="Unavailable" value="c" disabled options={OPTIONS} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Unavailable" });
    await user.click(trigger);
    expect(screen.queryByRole("listbox")).toBeNull();
    const { user: otherUser } = setup();
    await otherUser.click(screen.getByRole("button", { name: "Delivery option" }));
    expect(screen.getByRole("option", { name: "Charlie" })).toHaveAttribute("aria-disabled", "true");
  });

  it("Tab moves on from a portaled listbox without trapping focus", async () => {
    const user = userEvent.setup();
    render(<div>
      <button type="button">Previous</button>
      <Select label="Choose" options={OPTIONS} value="a" onChange={vi.fn()} />
      <button type="button">Next</button>
    </div>);
    await user.click(screen.getByRole("button", { name: "Choose" }));
    await user.keyboard("{Tab}");
    expect(screen.getByRole("button", { name: "Next" })).toHaveFocus();
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});

// Former chrome adapter regression cases, now exercised directly against the kit Select.
const LEGACY_OPTIONS = [
  { value: "a", label: "Option A" },
  { value: "b", label: "Option B", description: "The second choice" },
  { value: "c", label: "Option C" },
];

describe("Select", () => {
  it("shows the selected option's label on the trigger, not the placeholder", () => {
    render(<Select ariaLabel="Pick one" value="b" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Pick one" })).toHaveTextContent("Option B");
  });

  it("falls back to the placeholder when the current value matches no option", () => {
    render(<Select ariaLabel="Pick one" value="nonexistent" options={LEGACY_OPTIONS} onChange={vi.fn()} placeholder="Choose…" />);
    expect(screen.getByRole("button", { name: "Pick one" })).toHaveTextContent("Choose…");
  });

  it("the panel is closed by default and opens on trigger click, listing every option", async () => {
    const user = userEvent.setup();
    render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} />);

    expect(screen.queryByRole("listbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    const panel = screen.getByRole("listbox", { name: "Pick one" });
    expect(panel).toBeInTheDocument();
    for (const option of LEGACY_OPTIONS) {
      // A description remains part of a listbox option's accessible name.
      expect(screen.getByRole("option", { name: new RegExp("^" + option.label) })).toBeInTheDocument();
    }
  });

  it("renders a portaled panel as a direct child of document.body, not nested under the trigger", async () => {
    const user = userEvent.setup();
    const { container } = render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    const panel = screen.getByRole("listbox", { name: "Pick one" });
    expect(container.contains(panel)).toBe(false);
    expect(document.body.contains(panel)).toBe(true);
  });

  it("renders an option's description when given one", async () => {
    const user = userEvent.setup();
    render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    expect(screen.getByText("The second choice")).toBeInTheDocument();
  });

  it("marks the currently-selected option with the active class, and no other option", async () => {
    const user = userEvent.setup();
    render(<Select ariaLabel="Pick one" value="b" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));

    // Option B's own accessible name also folds in its description text -
    // see the "listing every option" test's own comment.
    expect(screen.getByRole("option", { name: /^Option B/ })).toHaveClass("is-active");
    expect(screen.getByRole("option", { name: "Option A" })).not.toHaveClass("is-active");
    expect(screen.getByRole("option", { name: "Option C" })).not.toHaveClass("is-active");
  });

  it("clicking an option calls onChange with its value and closes the panel", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={onChange} />);
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
        <Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={onChange} />
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

    render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Pick one" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("listbox")).toBeNull();
    expect(parentEscapeHandler).not.toHaveBeenCalled();
  });

  it("Escape returns focus to the trigger button", async () => {
    const user = userEvent.setup();
    render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Pick one" });
    await user.click(trigger);
    await user.keyboard("{Escape}");

    expect(trigger).toHaveFocus();
  });

  it("a disabled select cannot be opened", async () => {
    const user = userEvent.setup();
    render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} disabled />);

    const trigger = screen.getByRole("button", { name: "Pick one" });
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("sets aria-haspopup and aria-expanded reflecting the panel's real open state", async () => {
    const user = userEvent.setup();
    render(<Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: "Pick one" });

    expect(trigger).toHaveAttribute("aria-haspopup", "listbox");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  it("opens with ArrowDown, focuses the listbox at the next option, and supports Home/End/Enter", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Select ariaLabel="Pick one" value="b" options={LEGACY_OPTIONS} onChange={onChange} />);
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
        <Select ariaLabel="Pick one" value="a" options={LEGACY_OPTIONS} onChange={vi.fn()} />
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
      <Select ariaLabel="Pick one" value="b" options={LEGACY_OPTIONS} onChange={vi.fn()} />,
    );
    const trigger = screen.getByRole("button", { name: "Pick one" });
    expect(trigger).toHaveTextContent("Option B");
    expect(trigger).not.toHaveAccessibleDescription(/Selected:/);
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-controls", screen.getByRole("listbox").id);
    expect(screen.getByRole("option", { name: /^Option B/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: "Option A" })).toHaveAttribute("aria-selected", "false");
    await user.keyboard("{Escape}");
    rerender(<Select ariaLabel="Pick one" value="c" options={LEGACY_OPTIONS} onChange={vi.fn()} />);
    expect(trigger).toHaveTextContent("Option C");
    expect(trigger).not.toHaveAccessibleDescription(/Selected:/);
  });


});
