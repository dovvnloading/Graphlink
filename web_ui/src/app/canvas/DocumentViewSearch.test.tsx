import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocumentViewSearch } from "./DocumentViewSearch";

// A failed assertion inside a fake-timers test would otherwise skip past its
// own vi.useRealTimers() cleanup, leaving every later test in this file
// (several of which use userEvent, whose internal delays need real timers)
// hanging indefinitely - this guarantees real timers are restored
// regardless of how a test exits.
afterEach(() => {
  vi.useRealTimers();
});

function renderSearch(overrides: Partial<React.ComponentProps<typeof DocumentViewSearch>> = {}) {
  const props = {
    isOpen: true,
    query: "",
    onQueryChange: vi.fn(),
    matchCount: 0,
    currentMatchNumber: 0,
    onNext: vi.fn(),
    onPrevious: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return { ...render(<DocumentViewSearch {...props} />), props };
}

describe("DocumentViewSearch", () => {
  it("renders nothing when closed", () => {
    const { container } = renderSearch({ isOpen: false });
    expect(container.firstChild).toBeNull();
  });

  it("renders the input, focused, when open", () => {
    renderSearch();
    expect(screen.getByRole("textbox", { name: "Search query" })).toHaveFocus();
  });

  it("shows no count for an empty query", () => {
    renderSearch({ query: "", matchCount: 0, currentMatchNumber: 0 });
    expect(screen.queryByText(/of/)).toBeNull();
  });

  it("shows 'n of m' once there is a non-empty query, including the zero-match case", () => {
    const { container } = renderSearch({ query: "xyz", matchCount: 0, currentMatchNumber: 0 });
    expect(container.querySelector(".document-view-search-count")).toHaveTextContent("0 of 0");
  });

  it("shows the current match number out of the total", () => {
    const { container } = renderSearch({ query: "cat", matchCount: 5, currentMatchNumber: 3 });
    expect(container.querySelector(".document-view-search-count")).toHaveTextContent("3 of 5");
  });

  it("announces the count to screen readers via a debounced, visually-hidden live region", async () => {
    vi.useFakeTimers();
    const { rerender } = renderSearch({ query: "cat", matchCount: 1, currentMatchNumber: 1 });
    const liveRegion = () => screen.getByText((_, el) => el?.getAttribute("aria-live") === "polite");

    // The visible count updates instantly...
    expect(document.querySelector(".document-view-search-count")).toHaveTextContent("1 of 1");
    // ...but the live region hasn't caught up yet (debounced).
    expect(liveRegion()).toHaveTextContent("1 of 1");

    rerender(
      <DocumentViewSearch
        isOpen
        query="cat"
        onQueryChange={vi.fn()}
        matchCount={2}
        currentMatchNumber={2}
        onNext={vi.fn()}
        onPrevious={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(document.querySelector(".document-view-search-count")).toHaveTextContent("2 of 2");
    // Immediately after a change, the live region still reflects the PREVIOUS
    // settled value - it has not yet caught up.
    expect(liveRegion()).toHaveTextContent("1 of 1");

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(liveRegion()).toHaveTextContent("2 of 2");
    vi.useRealTimers();
  });

  it("calls onQueryChange as the user types", async () => {
    const user = userEvent.setup();
    const { props } = renderSearch();
    await user.type(screen.getByRole("textbox", { name: "Search query" }), "a");
    expect(props.onQueryChange).toHaveBeenCalledWith("a");
  });

  it("disables Previous/Next when there are no matches", () => {
    renderSearch({ matchCount: 0 });
    expect(screen.getByRole("button", { name: "Previous match" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next match" })).toBeDisabled();
  });

  it("enables and wires up Previous/Next when there are matches", async () => {
    const user = userEvent.setup();
    const { props } = renderSearch({ matchCount: 3, currentMatchNumber: 1 });
    const next = screen.getByRole("button", { name: "Next match" });
    const previous = screen.getByRole("button", { name: "Previous match" });
    expect(next).toBeEnabled();
    expect(previous).toBeEnabled();

    await user.click(next);
    expect(props.onNext).toHaveBeenCalledOnce();
    await user.click(previous);
    expect(props.onPrevious).toHaveBeenCalledOnce();
  });

  it("Enter in the input calls onNext, Shift+Enter calls onPrevious", async () => {
    const user = userEvent.setup();
    const { props } = renderSearch({ matchCount: 2, currentMatchNumber: 1 });
    const input = screen.getByRole("textbox", { name: "Search query" });

    await user.type(input, "{Enter}");
    expect(props.onNext).toHaveBeenCalledOnce();

    await user.type(input, "{Shift>}{Enter}{/Shift}");
    expect(props.onPrevious).toHaveBeenCalledOnce();
  });

  it("Escape in the input calls onClose", async () => {
    const user = userEvent.setup();
    const { props } = renderSearch();
    await user.type(screen.getByRole("textbox", { name: "Search query" }), "{Escape}");
    expect(props.onClose).toHaveBeenCalledOnce();
  });

  it("the close button calls onClose", async () => {
    const user = userEvent.setup();
    const { props } = renderSearch();
    await user.click(screen.getByRole("button", { name: "Close search" }));
    expect(props.onClose).toHaveBeenCalledOnce();
  });
});
