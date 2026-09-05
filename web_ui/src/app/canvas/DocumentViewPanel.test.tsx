import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentViewPanel } from "./DocumentViewPanel";

// jsdom implements no IntersectionObserver - DocumentViewToc.tsx's own
// scrollspy effect needs one the moment its dropdown is actually opened
// (most tests here never open it, but the stage-3 ToC/search interaction
// test below does). Same minimal fake DocumentViewToc.test.tsx uses.
class FakeIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

beforeEach(() => {
  // @ts-expect-error - test double, not the real browser API
  global.IntersectionObserver = FakeIntersectionObserver;
});

// The panel's body arrives through a React.lazy dynamic import. In a built
// bundle that is a prebuilt chunk; under vitest it is the FIRST transform of
// react-markdown plus six remark/rehype plugins, and with 93 test files
// competing for the worker pool that regularly runs past testing-library's
// 1000ms default - two full-suite runs in three, measured. The wait is long
// because the test environment is slow to compile the module graph, not
// because the panel is slow, so the right fix is to wait properly rather
// than to reach for fake timers or to stub the import away and stop testing
// the thing that broke.
const LAZY_BODY_TIMEOUT = { timeout: 15_000 };

function renderPanel(overrides: Partial<React.ComponentProps<typeof DocumentViewPanel>> = {}) {
  const props = {
    isOpen: true,
    content: "some content",
    sourceLabel: null as string | null,
    onClose: vi.fn(),
    ...overrides,
  };
  return render(<DocumentViewPanel {...props} />);
}

describe("DocumentViewPanel", () => {
  it("renders the fixed title and the passed markdown content", async () => {
    renderPanel({ content: "# Heading\n\nA paragraph of body text." });

    // The title is part of the eager shell; the body arrives with the lazy
    // markdown chunk, hence findBy rather than getBy.
    expect(screen.getByText("Document View")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Heading" }, LAZY_BODY_TIMEOUT)).toBeInTheDocument();
    expect(screen.getByText("A paragraph of body text.")).toBeInTheDocument();
  });

  it("does not crash and renders an empty body when content is null", () => {
    renderPanel({ content: null });
    expect(screen.getByText("Document View")).toBeInTheDocument();
  });

  it("renders the source label as a subtitle when provided", () => {
    renderPanel({ sourceLabel: "Conversation transcript" });
    expect(screen.getByText("Conversation transcript")).toBeInTheDocument();
  });

  it("renders no subtitle when sourceLabel is null", () => {
    renderPanel({ sourceLabel: null });
    expect(screen.queryByText("Conversation transcript")).toBeNull();
  });

  it("clicking Close calls onClose - the only way this panel closes, unlike a Dialog", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderPanel({ onClose });

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders as a plain landmark region, not a modal dialog (no role='dialog', no scrim)", () => {
    renderPanel();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByLabelText("Document View")).toBeInTheDocument();
  });

  it("collapses to zero width and is aria-hidden when closed", () => {
    const { container } = renderPanel({ isOpen: false, content: "still here" });

    const panel = container.querySelector(".document-view-panel") as HTMLElement;
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel.style.width).toBe("0px");
  });

  it("does not load its markdown chunk for a panel that has never been opened", () => {
    // The deferral this panel's lazy split exists for: the shell mounts with
    // the app, the ~130 KB of markdown machinery behind it does not, until
    // somebody actually opens the Document View.
    renderPanel({ isOpen: false, content: "still here" });
    expect(screen.queryByText("still here")).toBeNull();
  });

  it("keeps its content mounted after being closed again, so reopening does not flicker", async () => {
    const { rerender } = renderPanel({ isOpen: true, content: "still here" });
    expect(await screen.findByText("still here", undefined, LAZY_BODY_TIMEOUT)).toBeInTheDocument();

    rerender(
      <DocumentViewPanel isOpen={false} content="still here" sourceLabel={null} onClose={vi.fn()} />,
    );

    // Closed, but never unmounted - the property the eager version had, kept
    // by latching "has been opened" rather than tracking `isOpen`.
    expect(screen.getByText("still here")).toBeInTheDocument();
  });

  it("is not aria-hidden and has a real width when open", () => {
    const { container } = renderPanel({ isOpen: true });

    const panel = container.querySelector(".document-view-panel") as HTMLElement;
    expect(panel).toHaveAttribute("aria-hidden", "false");
    expect(panel.style.width).toBe("500px");
  });

  it("renders a resize handle", () => {
    renderPanel();
    expect(screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." })).toBeInTheDocument();
  });

  it("dragging the resize handle changes the panel's width, and releasing ends the drag cleanly", () => {
    const { container } = renderPanel();
    const handle = screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." });
    const panel = container.querySelector(".document-view-panel") as HTMLElement;

    fireEvent.pointerDown(handle, { clientX: 500, pointerId: 1 });
    expect(panel.className).toContain("document-view-panel-resizing");

    fireEvent.pointerMove(handle, { clientX: 600, pointerId: 1 });
    expect(panel.style.width).toBe("600px");

    fireEvent.pointerUp(handle, { clientX: 600, pointerId: 1 });
    expect(panel.className).not.toContain("document-view-panel-resizing");

    // The drag has genuinely ended, not just visually - a stray pointermove
    // anywhere afterward (this is exactly the class of bug a dangling
    // window-level listener would have let slip through) must not keep
    // resizing the panel.
    fireEvent.pointerMove(handle, { clientX: 900, pointerId: 1 });
    expect(panel.style.width).toBe("600px");
  });

  it("clamps the resize width to the configured min/max range", () => {
    const { container } = renderPanel();
    const handle = screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." });
    const panel = container.querySelector(".document-view-panel") as HTMLElement;

    fireEvent.pointerDown(handle, { clientX: 500, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: -5000, pointerId: 1 });
    expect(panel.style.width).toBe("320px");

    fireEvent.pointerMove(handle, { clientX: 5000, pointerId: 1 });
    expect(panel.style.width).toBe("900px");
  });

  describe("Copy button", () => {
    // userEvent.setup() installs its OWN navigator.clipboard stub internally
    // (so .copy()/.paste() are testable) - defining a mock clipboard BEFORE
    // calling setup() gets silently clobbered the moment setup() runs. The
    // fix is ordering: define the mock AFTER setup(), so it's the last
    // writer, not a beforeEach that necessarily runs first.
    function mockClipboard(): ReturnType<typeof vi.fn> {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText },
        configurable: true,
        writable: true,
      });
      return writeText;
    }

    it("is disabled when there is no content", () => {
      renderPanel({ content: null });
      expect(screen.getByRole("button", { name: "Copy content" })).toBeDisabled();
    });

    it("copies the content and flashes 'Copied' on click", async () => {
      const user = userEvent.setup();
      const writeText = mockClipboard();
      renderPanel({ content: "copy me" });

      await user.click(screen.getByRole("button", { name: "Copy content" }));

      expect(writeText).toHaveBeenCalledWith("copy me");
      expect(await screen.findByText("Copied")).toBeInTheDocument();
    });
  });

  // Document View full redesign, stage 2 ("table of contents + reading
  // progress"). The outline toggle's own detailed behavior (opening,
  // active-section highlighting, scroll-to-heading) is covered in
  // DocumentViewToc.test.tsx - these tests only cover DocumentViewPanel's
  // own wiring: whether the toggle shows up at all for a given content
  // shape, the reading-progress bar's scroll-driven width, and the
  // reset-on-new-content behavior.
  describe("table of contents + reading progress (stage 2)", () => {
    it("shows the Outline toggle when the content has 2+ headings", async () => {
      renderPanel({ content: "# One\n\n## Two" });
      // Headings come from a dynamically imported parser, so they land a
      // microtask after mount rather than during it.
      expect(
        await screen.findByRole("button", { name: "Outline" }, LAZY_BODY_TIMEOUT),
      ).toBeInTheDocument();
    });

    it("shows no Outline toggle when the content has fewer than 2 headings", () => {
      renderPanel({ content: "just a paragraph, no headings" });
      expect(screen.queryByRole("button", { name: "Outline" })).toBeNull();
    });

    it("renders a reading-progress bar starting at 0", () => {
      renderPanel({ content: "some content" });
      const bar = screen.getByRole("progressbar", { name: "Reading progress" });
      expect(bar).toHaveAttribute("aria-valuenow", "0");
    });

    it("updates the reading-progress bar as the content area scrolls", () => {
      const { container } = renderPanel({ content: "some content" });
      const scrollArea = container.querySelector(".document-view-panel-scroll") as HTMLDivElement;
      Object.defineProperty(scrollArea, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(scrollArea, "clientHeight", { value: 500, configurable: true });
      scrollArea.scrollTop = 250;

      fireEvent.scroll(scrollArea);

      const bar = screen.getByRole("progressbar", { name: "Reading progress" });
      // 250 / (1000 - 500) * 100 = 50
      expect(bar).toHaveAttribute("aria-valuenow", "50");
    });

    it("resets scroll position and reading progress when content changes to a new document", () => {
      const { container, rerender } = renderPanel({ content: "first document" });
      const scrollArea = container.querySelector(".document-view-panel-scroll") as HTMLDivElement;
      Object.defineProperty(scrollArea, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(scrollArea, "clientHeight", { value: 500, configurable: true });
      scrollArea.scrollTop = 400;
      fireEvent.scroll(scrollArea);
      expect(screen.getByRole("progressbar", { name: "Reading progress" })).toHaveAttribute("aria-valuenow", "80");

      rerender(<DocumentViewPanel isOpen content="a completely different document" sourceLabel={null} onClose={vi.fn()} />);

      expect(scrollArea.scrollTop).toBe(0);
      expect(screen.getByRole("progressbar", { name: "Reading progress" })).toHaveAttribute("aria-valuenow", "0");
    });
  });

  // Document View full redesign, stage 3 ("in-document search/find"). The
  // search bar's own detailed UI behavior (typing, Enter/Shift+Enter,
  // Escape, disabled states) is covered in DocumentViewSearch.test.tsx, and
  // the highlighting/matching logic itself in
  // documentViewSearchHighlight.test.ts - these tests cover only
  // DocumentViewPanel's own wiring: deriving match count and the
  // current-match highlight from the real rendered <mark> elements, and the
  // reset-on-new-content behavior.
  describe("in-document search/find (stage 3)", () => {
    it("shows the Find toggle, disabled when there is no content", () => {
      renderPanel({ content: null });
      expect(screen.getByRole("button", { name: "Find in document" })).toBeDisabled();
    });

    it("opens the search bar on Find click", async () => {
      const user = userEvent.setup();
      renderPanel({ content: "the cat sat on the mat" });

      await user.click(screen.getByRole("button", { name: "Find in document" }));
      expect(screen.getByRole("search")).toBeInTheDocument();
    });

    function countText(container: HTMLElement): string | null {
      return container.querySelector(".document-view-search-count")?.textContent ?? null;
    }

    it("typing a query highlights every match and shows a match count, starting on the first match", async () => {
      const user = userEvent.setup();
      const { container } = renderPanel({ content: "the cat sat on the cat mat" });

      await user.click(screen.getByRole("button", { name: "Find in document" }));
      await user.type(screen.getByRole("textbox", { name: "Search query" }), "cat");

      const matches = container.querySelectorAll(".document-view-search-match");
      expect(matches).toHaveLength(2);
      expect(countText(container)).toBe("1 of 2");
      expect(matches[0]).toHaveClass("document-view-search-match-current");
      expect(matches[1]).not.toHaveClass("document-view-search-match-current");
    });

    it("Next/Previous move the current-match highlight, wrapping around at either end", async () => {
      const user = userEvent.setup();
      const { container } = renderPanel({ content: "the cat sat on the cat mat" });
      await user.click(screen.getByRole("button", { name: "Find in document" }));
      await user.type(screen.getByRole("textbox", { name: "Search query" }), "cat");

      await user.click(screen.getByRole("button", { name: "Next match" }));
      let matches = container.querySelectorAll(".document-view-search-match");
      expect(matches[1]).toHaveClass("document-view-search-match-current");
      expect(countText(container)).toBe("2 of 2");

      // Wraps from the last match back to the first.
      await user.click(screen.getByRole("button", { name: "Next match" }));
      matches = container.querySelectorAll(".document-view-search-match");
      expect(matches[0]).toHaveClass("document-view-search-match-current");
      expect(countText(container)).toBe("1 of 2");

      // Wraps from the first match back to the last, going the other way.
      await user.click(screen.getByRole("button", { name: "Previous match" }));
      matches = container.querySelectorAll(".document-view-search-match");
      expect(matches[1]).toHaveClass("document-view-search-match-current");
      expect(countText(container)).toBe("2 of 2");
    });

    it("closing the search bar clears all highlighting", async () => {
      const user = userEvent.setup();
      const { container } = renderPanel({ content: "the cat sat on the cat mat" });
      await user.click(screen.getByRole("button", { name: "Find in document" }));
      await user.type(screen.getByRole("textbox", { name: "Search query" }), "cat");
      expect(container.querySelectorAll(".document-view-search-match")).toHaveLength(2);

      await user.click(screen.getByRole("button", { name: "Close search" }));

      expect(screen.queryByRole("search")).toBeNull();
      expect(container.querySelectorAll(".document-view-search-match")).toHaveLength(0);
    });

    it("clicking the header Find toggle closed also clears highlighting, the same as the bar's own Close button", async () => {
      // Regression test: the toggle button's onClick originally only
      // flipped isSearchOpen, leaving searchQuery (and therefore every
      // highlighted <mark>) untouched - closing the search UI via the
      // header toggle left matches permanently stuck highlighted with no
      // visible control left to clear them. Caught by adversarial review,
      // confirmed independently by three separate reviewers.
      const user = userEvent.setup();
      const { container } = renderPanel({ content: "the cat sat on the cat mat" });
      const findToggle = screen.getByRole("button", { name: "Find in document" });

      await user.click(findToggle);
      await user.type(screen.getByRole("textbox", { name: "Search query" }), "cat");
      expect(container.querySelectorAll(".document-view-search-match")).toHaveLength(2);

      await user.click(findToggle);

      expect(screen.queryByRole("search")).toBeNull();
      expect(container.querySelectorAll(".document-view-search-match")).toHaveLength(0);
    });

    it("resets the search bar and clears highlighting when content changes to a new document", async () => {
      const user = userEvent.setup();
      const { container, rerender } = renderPanel({ content: "the cat sat" });
      await user.click(screen.getByRole("button", { name: "Find in document" }));
      await user.type(screen.getByRole("textbox", { name: "Search query" }), "cat");
      expect(container.querySelectorAll(".document-view-search-match")).toHaveLength(1);

      rerender(
        <DocumentViewPanel isOpen content="a completely different document" sourceLabel={null} onClose={vi.fn()} />,
      );

      expect(screen.queryByRole("search")).toBeNull();
      expect(container.querySelectorAll(".document-view-search-match")).toHaveLength(0);
    });

    it("Escape in the search input closes only the search bar, even with the ToC outline also open", async () => {
      // Regression test: DocumentViewToc's own Escape listener originally
      // ran in the capture phase, which always fires before the search
      // input's bubble-phase stopPropagation() could take effect - so
      // pressing Escape to dismiss just the search bar silently closed the
      // ToC outline too. Caught by adversarial review.
      // Order matters for this repro: opening search FIRST, then ToC,
      // avoids ToC's own (unrelated, pre-existing) outside-pointerdown-close
      // handler from closing it again before Escape is even pressed -
      // clicking ToC's own "Outline" toggle is inside its own root, not an
      // "outside" click, so this is the one ordering where both stay open
      // at once, matching the actual scenario adversarial review found.
      const user = userEvent.setup();
      renderPanel({ content: "# One\n\n## Two\n\nthe cat sat" });

      await user.click(screen.getByRole("button", { name: "Find in document" }));
      await user.click(screen.getByRole("button", { name: "Outline" }));
      expect(screen.getByRole("menu", { name: "Table of contents" })).toBeInTheDocument();
      expect(screen.getByRole("search")).toBeInTheDocument();

      const searchInput = screen.getByRole("textbox", { name: "Search query" });
      searchInput.focus();
      await user.keyboard("{Escape}");

      expect(screen.queryByRole("search")).toBeNull();
      expect(screen.getByRole("menu", { name: "Table of contents" })).toBeInTheDocument();
    });
  });

  describe("drawer UX polish (stage 4)", () => {
    it("Escape closes the panel", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderPanel({ onClose });

      await user.keyboard("{Escape}");
      expect(onClose).toHaveBeenCalledOnce();
    });

    it("does not close the panel on Escape while the search bar is open - closes search instead", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderPanel({ onClose, content: "the cat sat" });

      await user.click(screen.getByRole("button", { name: "Find in document" }));
      expect(screen.getByRole("search")).toBeInTheDocument();

      await user.keyboard("{Escape}");

      expect(onClose).not.toHaveBeenCalled();
      expect(screen.queryByRole("search")).toBeNull();
    });

    it("closes only the search bar via the panel's own Escape handling when focus has moved elsewhere in the panel (not the search input itself)", async () => {
      // Regression test: the test above passes even with the panel's own
      // isSearchOpen deferral branch deleted entirely, because the search
      // input auto-focuses and its OWN pre-existing (stage 3) Escape
      // handler intercepts the keystroke first, via stopPropagation, before
      // it ever reaches the panel's document-level listener. Caught by
      // adversarial review: that left the actual new branch this stage
      // added completely untested. Moving focus off the input first forces
      // the keystroke through the panel's own listener instead.
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderPanel({ onClose, content: "the cat sat" });

      await user.click(screen.getByRole("button", { name: "Find in document" }));
      const searchInput = screen.getByRole("textbox", { name: "Search query" });
      expect(searchInput).toHaveFocus();
      searchInput.blur();
      expect(document.activeElement).not.toBe(searchInput);

      await user.keyboard("{Escape}");

      expect(onClose).not.toHaveBeenCalled();
      expect(screen.queryByRole("search")).toBeNull();
    });

    it("closes only one of search/ToC (not both) from a single Escape press when both are open and focus is outside the search input", async () => {
      // Regression test for a confirmed adversarial-review finding: the
      // panel's own isSearchOpen branch didn't stop the event from also
      // reaching DocumentViewToc's own, separate document-level Escape
      // listener, so with both open and focus left on the Outline toggle
      // (not inside the search input), a single Escape press closed BOTH at
      // once instead of just one.
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderPanel({ onClose, content: "# One\n\n## Two\n\nthe cat sat" });

      await user.click(screen.getByRole("button", { name: "Find in document" }));
      await user.click(screen.getByRole("button", { name: "Outline" }));
      expect(screen.getByRole("search")).toBeInTheDocument();
      expect(screen.getByRole("menu", { name: "Table of contents" })).toBeInTheDocument();

      await user.keyboard("{Escape}");

      expect(onClose).not.toHaveBeenCalled();
      const searchStillOpen = screen.queryByRole("search") !== null;
      const tocStillOpen = screen.queryByRole("menu") !== null;
      expect(searchStillOpen).not.toBe(tocStillOpen);
    });

    it("does not close the panel on Escape while the ToC outline is open - closes the outline instead", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderPanel({ onClose, content: "# One\n\n## Two" });

      await user.click(await screen.findByRole("button", { name: "Outline" }, LAZY_BODY_TIMEOUT));
      expect(screen.getByRole("menu", { name: "Table of contents" })).toBeInTheDocument();

      await user.keyboard("{Escape}");

      expect(onClose).not.toHaveBeenCalled();
      expect(screen.queryByRole("menu")).toBeNull();
    });

    it("does not listen for Escape while closed", async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      renderPanel({ onClose, isOpen: false });

      await user.keyboard("{Escape}");
      expect(onClose).not.toHaveBeenCalled();
    });

    it("double-clicking the resize handle resets the width to the default", () => {
      const { container } = renderPanel();
      const handle = screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." });
      const panel = container.querySelector(".document-view-panel") as HTMLElement;

      fireEvent.pointerDown(handle, { clientX: 500, pointerId: 1 });
      fireEvent.pointerMove(handle, { clientX: 700, pointerId: 1 });
      fireEvent.pointerUp(handle, { clientX: 700, pointerId: 1 });
      expect(panel.style.width).toBe("700px");

      fireEvent.doubleClick(handle);
      expect(panel.style.width).toBe("500px");
    });

    it("is keyboard-focusable and pressing Enter resets the width to the default", () => {
      // Regression test for a confirmed adversarial-review finding: the
      // resize handle's double-click-to-reset had no keyboard equivalent -
      // a plain, non-focusable div with no way to trigger it without a
      // mouse.
      const { container } = renderPanel();
      const handle = screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." });
      const panel = container.querySelector(".document-view-panel") as HTMLElement;
      expect(handle).toHaveAttribute("tabIndex", "0");

      fireEvent.pointerDown(handle, { clientX: 500, pointerId: 1 });
      fireEvent.pointerMove(handle, { clientX: 700, pointerId: 1 });
      fireEvent.pointerUp(handle, { clientX: 700, pointerId: 1 });
      expect(panel.style.width).toBe("700px");

      fireEvent.keyDown(handle, { key: "Enter" });
      expect(panel.style.width).toBe("500px");
    });

    it("pressing Space on the focused resize handle also resets the width", () => {
      const { container } = renderPanel();
      const handle = screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." });
      const panel = container.querySelector(".document-view-panel") as HTMLElement;

      fireEvent.pointerDown(handle, { clientX: 500, pointerId: 1 });
      fireEvent.pointerMove(handle, { clientX: 650, pointerId: 1 });
      fireEvent.pointerUp(handle, { clientX: 650, pointerId: 1 });
      expect(panel.style.width).toBe("650px");

      fireEvent.keyDown(handle, { key: " " });
      expect(panel.style.width).toBe("500px");
    });

    describe("Expand/Collapse toggle", () => {
      it("expanding removes the inline width so the CSS class takes over, and shows 'Collapse'", () => {
        const { container } = renderPanel();
        const panel = container.querySelector(".document-view-panel") as HTMLElement;
        expect(panel.style.width).toBe("500px");

        fireEvent.click(screen.getByRole("button", { name: "Expand" }));

        expect(panel).toHaveClass("document-view-panel-expanded");
        expect(panel.style.width).toBe("");
        expect(screen.getByRole("button", { name: "Collapse" })).toBeInTheDocument();
      });

      it("collapsing restores the exact width from before expanding", () => {
        const { container } = renderPanel();
        const panel = container.querySelector(".document-view-panel") as HTMLElement;
        const handle = screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." });

        fireEvent.pointerDown(handle, { clientX: 500, pointerId: 1 });
        fireEvent.pointerMove(handle, { clientX: 650, pointerId: 1 });
        fireEvent.pointerUp(handle, { clientX: 650, pointerId: 1 });
        expect(panel.style.width).toBe("650px");

        fireEvent.click(screen.getByRole("button", { name: "Expand" }));
        expect(panel).not.toHaveClass("document-view-panel-resizing");
        fireEvent.click(screen.getByRole("button", { name: "Collapse" }));

        expect(panel).not.toHaveClass("document-view-panel-expanded");
        expect(panel.style.width).toBe("650px");
      });

      it("starting a manual drag while expanded exits expanded mode", () => {
        const { container } = renderPanel();
        const panel = container.querySelector(".document-view-panel") as HTMLElement;
        const handle = screen.getByRole("separator", { name: "Resize Document View panel. Press Enter to reset to the default width." });

        fireEvent.click(screen.getByRole("button", { name: "Expand" }));
        expect(panel).toHaveClass("document-view-panel-expanded");

        fireEvent.pointerDown(handle, { clientX: 500, pointerId: 1 });
        expect(panel).not.toHaveClass("document-view-panel-expanded");

        fireEvent.pointerMove(handle, { clientX: 600, pointerId: 1 });
        expect(panel.style.width).toBe("600px");
      });
    });

    describe("font-size stepper", () => {
      function scrollFontSize(container: HTMLElement): string {
        return (container.querySelector(".document-view-panel-scroll") as HTMLElement).style.fontSize;
      }

      it("applies no inline font-size at the default step", () => {
        const { container } = renderPanel();
        expect(scrollFontSize(container)).toBe("");
      });

      it("increasing steps up applies a larger em multiplier", () => {
        const { container } = renderPanel();
        fireEvent.click(screen.getByRole("button", { name: "Increase text size" }));
        expect(scrollFontSize(container)).toBe("1.15em");
      });

      it("decreasing steps down applies a smaller em multiplier", () => {
        const { container } = renderPanel();
        fireEvent.click(screen.getByRole("button", { name: "Decrease text size" }));
        expect(scrollFontSize(container)).toBe("0.85em");
      });

      it("returning to the default step removes the inline font-size again", () => {
        const { container } = renderPanel();
        fireEvent.click(screen.getByRole("button", { name: "Increase text size" }));
        expect(scrollFontSize(container)).toBe("1.15em");

        fireEvent.click(screen.getByRole("button", { name: "Decrease text size" }));
        expect(scrollFontSize(container)).toBe("");
      });

      it("disables Decrease at the smallest step and Increase at the largest", () => {
        renderPanel();
        const decrease = screen.getByRole("button", { name: "Decrease text size" });
        const increase = screen.getByRole("button", { name: "Increase text size" });

        fireEvent.click(decrease);
        expect(decrease).toBeDisabled();

        fireEvent.click(increase);
        fireEvent.click(increase);
        fireEvent.click(increase);
        expect(increase).toBeDisabled();
      });
    });
  });
});
