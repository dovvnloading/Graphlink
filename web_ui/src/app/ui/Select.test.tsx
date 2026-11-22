import { render, screen } from "@testing-library/react";
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
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Delta" }).id);
    await user.keyboard("{Home}");
    expect(trigger).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Alpha" }).id);
    await user.keyboard("{End}{ArrowDown}{ArrowUp}{Enter}");
    expect(onChange).toHaveBeenCalledWith("Echo");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("supports incremental type-ahead by option label", async () => {
    const { user, trigger, onChange } = setup();
    await user.click(trigger);
    await user.keyboard("d");
    expect(trigger).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Delta" }).id);
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
