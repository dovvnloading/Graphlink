import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "./SegmentedControl";

const options = [
  { value: "list", label: "List", icon: "document-text" as const },
  { value: "grid", label: "Grid", icon: "grid-lines" as const },
  { value: "cards", label: "Cards" },
];

describe("SegmentedControl", () => {
  it("renders a labelled radiogroup with one checked and tabbable radio", () => {
    render(<SegmentedControl label="View mode" options={options} />);
    const group = screen.getByRole("radiogroup", { name: "View mode" });
    expect(group).toHaveClass("gl-segmented-control--md");
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(radios[0]).toHaveAccessibleName("List");
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[0]).toHaveAttribute("tabindex", "0");
    expect(radios[1]).toHaveAttribute("tabindex", "-1");
  });

  it("moves, wraps and selects with both axes of arrow keys", () => {
    render(<SegmentedControl label="View mode" options={options} />);
    const [list, grid, cards] = screen.getAllByRole("radio");
    list.focus();
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(grid).toHaveFocus();
    expect(grid).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    expect(cards).toHaveFocus();
    fireEvent.keyDown(cards, { key: "ArrowRight" });
    expect(list).toHaveFocus();
    fireEvent.keyDown(list, { key: "ArrowLeft" });
    expect(cards).toHaveFocus();
    fireEvent.keyDown(cards, { key: "ArrowUp" });
    expect(grid).toHaveFocus();
  });

  it("supports Home, End and clicking a selected choice", () => {
    const onChange = vi.fn();
    render(<SegmentedControl label="View mode" options={options} defaultValue="grid" onChange={onChange} size="sm" />);
    const [list, grid, cards] = screen.getAllByRole("radio");
    expect(screen.getByRole("radiogroup")).toHaveClass("gl-segmented-control--sm");
    fireEvent.keyDown(grid, { key: "End" });
    expect(cards).toHaveFocus();
    expect(onChange).toHaveBeenLastCalledWith("cards");
    fireEvent.keyDown(cards, { key: "Home" });
    expect(list).toHaveFocus();
    expect(onChange).toHaveBeenLastCalledWith("list");
    fireEvent.click(list);
    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it("honours controlled value, calls onChange and exposes the selected accent class", () => {
    const onChange = vi.fn();
    const { rerender } = render(<SegmentedControl label="View mode" options={options} value="grid" onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "Grid" })).toHaveClass("gl-segmented-control-segment--selected");
    fireEvent.click(screen.getByRole("radio", { name: "Cards" }));
    expect(onChange).toHaveBeenCalledWith("cards");
    expect(screen.getByRole("radio", { name: "Grid" })).toHaveAttribute("aria-checked", "true");
    rerender(<SegmentedControl label="View mode" options={options} value="cards" onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "Cards" })).toHaveAttribute("aria-checked", "true");
  });

  it("disables every radio and navigation, and handles an empty option list", () => {
    const onChange = vi.fn();
    const { rerender } = render(<SegmentedControl label="View mode" options={options} disabled onChange={onChange} />);
    const group = screen.getByRole("radiogroup");
    expect(group).toHaveAttribute("aria-disabled", "true");
    const radios = screen.getAllByRole("radio");
    radios.forEach((radio) => expect(radio).toBeDisabled());
    fireEvent.keyDown(radios[0], { key: "ArrowRight" });
    fireEvent.click(radios[1]);
    expect(onChange).not.toHaveBeenCalled();
    rerender(<SegmentedControl label="View mode" options={[]} />);
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
  });
});
