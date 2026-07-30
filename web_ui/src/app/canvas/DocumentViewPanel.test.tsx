import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DocumentViewPanel } from "./DocumentViewPanel";

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
});
