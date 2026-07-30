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
  it("renders the fixed title and the passed markdown content", () => {
    renderPanel({ content: "# Heading\n\nA paragraph of body text." });

    expect(screen.getByText("Document View")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
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

  it("collapses to zero width and is aria-hidden when closed, without unmounting", () => {
    const { container } = renderPanel({ isOpen: false, content: "still here" });

    const panel = container.querySelector(".document-view-panel") as HTMLElement;
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel.style.width).toBe("0px");
    // Content stays mounted (no unmount/remount flicker on next open) -
    // just clipped by the closed panel's own overflow:hidden.
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
    expect(screen.getByRole("separator", { name: "Resize Document View panel" })).toBeInTheDocument();
  });

  it("dragging the resize handle changes the panel's width, and releasing ends the drag cleanly", () => {
    const { container } = renderPanel();
    const handle = screen.getByRole("separator", { name: "Resize Document View panel" });
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
    const handle = screen.getByRole("separator", { name: "Resize Document View panel" });
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
    it("shows the Outline toggle when the content has 2+ headings", () => {
      renderPanel({ content: "# One\n\n## Two" });
      expect(screen.getByRole("button", { name: "Outline" })).toBeInTheDocument();
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
});
