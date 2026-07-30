import { createRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentViewToc } from "./DocumentViewToc";
import type { DocumentHeading } from "./documentViewHeadings";

// Document View full redesign, stage 2 ("table of contents + reading
// progress"): the outline toggle + dropdown. jsdom implements neither
// IntersectionObserver nor real layout (getBoundingClientRect always
// returns all-zero rects) - both are hand-faked below, the standard
// pattern for testing scrollspy/IntersectionObserver-driven components.

class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = [];
  callback: IntersectionObserverCallback;
  observed: Element[] = [];
  disconnected = false;
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    FakeIntersectionObserver.instances.push(this);
  }
  observe(el: Element) {
    this.observed.push(el);
  }
  unobserve() {}
  disconnect() {
    this.disconnected = true;
  }
  takeRecords() {
    return [];
  }
}

beforeEach(() => {
  FakeIntersectionObserver.instances = [];
  // @ts-expect-error - test double, not the real browser API
  global.IntersectionObserver = FakeIntersectionObserver;
});

afterEach(() => {
  vi.restoreAllMocks();
});

const HEADINGS: DocumentHeading[] = [
  { depth: 1, text: "Title", id: "title" },
  { depth: 2, text: "Section One", id: "section-one" },
  { depth: 3, text: "Subsection", id: "subsection" },
];

function renderToc(headings: DocumentHeading[] = HEADINGS) {
  const scrollContainerRef = createRef<HTMLDivElement>();
  const utils = render(
    <div>
      <DocumentViewToc headings={headings} scrollContainerRef={scrollContainerRef} />
      {/* Real target elements for scroll-to / IntersectionObserver wiring to find by id. */}
      <div ref={scrollContainerRef}>
        {headings.map((h) => (
          <div key={h.id} id={h.id}>
            {h.text}
          </div>
        ))}
      </div>
    </div>,
  );
  return { ...utils, scrollContainerRef };
}

describe("DocumentViewToc", () => {
  it("renders nothing when there are fewer than 2 headings", () => {
    const { container } = renderToc([{ depth: 1, text: "Only One", id: "only-one" }]);
    expect(container.querySelector(".document-view-toc")).toBeNull();
  });

  it("renders the Outline toggle when there are 2 or more headings", () => {
    renderToc();
    expect(screen.getByRole("button", { name: "Outline" })).toBeInTheDocument();
  });

  it("opens the dropdown on click, listing every heading's text", async () => {
    const user = userEvent.setup();
    renderToc();
    const toggle = screen.getByRole("button", { name: "Outline" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu", { name: "Table of contents" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Title" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Section One" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Subsection" })).toBeInTheDocument();
  });

  it("clicking the toggle again closes the dropdown", async () => {
    const user = userEvent.setup();
    renderToc();
    const toggle = screen.getByRole("button", { name: "Outline" });
    await user.click(toggle);
    await user.click(toggle);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Escape closes the dropdown", async () => {
    const user = userEvent.setup();
    renderToc();
    await user.click(screen.getByRole("button", { name: "Outline" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a click outside the dropdown closes it", async () => {
    const user = userEvent.setup();
    renderToc();
    await user.click(screen.getByRole("button", { name: "Outline" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("clicking a heading scrolls the container by the heading's own offset and closes the dropdown", async () => {
    const user = userEvent.setup();
    const { scrollContainerRef } = renderToc();
    const container = scrollContainerRef.current as HTMLDivElement;
    container.scrollTop = 50;
    vi.spyOn(container, "getBoundingClientRect").mockReturnValue({ top: 100 } as DOMRect);
    vi.spyOn(document.getElementById("subsection")!, "getBoundingClientRect").mockReturnValue({
      top: 340,
    } as DOMRect);

    await user.click(screen.getByRole("button", { name: "Outline" }));
    await user.click(screen.getByRole("menuitem", { name: "Subsection" }));

    // 50 (starting scrollTop) + (340 - 100) = 290
    expect(container.scrollTop).toBe(290);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("sets up an IntersectionObserver over every heading element once opened, and highlights the one it reports as active", async () => {
    const user = userEvent.setup();
    renderToc();
    await user.click(screen.getByRole("button", { name: "Outline" }));

    const observer = FakeIntersectionObserver.instances.at(-1)!;
    expect(observer.observed.map((el) => el.id)).toEqual(["title", "section-one", "subsection"]);

    const sectionOneEl = document.getElementById("section-one")!;
    act(() => {
      observer.callback(
        [
          {
            target: sectionOneEl,
            isIntersecting: true,
            boundingClientRect: { top: 10 },
          } as unknown as IntersectionObserverEntry,
        ],
        observer as unknown as IntersectionObserver,
      );
    });

    expect(screen.getByRole("menuitem", { name: "Section One" }).className).toContain("active");
    expect(screen.getByRole("menuitem", { name: "Title" }).className).not.toContain("active");
  });

  it("disconnects the observer when the dropdown closes", async () => {
    const user = userEvent.setup();
    renderToc();
    await user.click(screen.getByRole("button", { name: "Outline" }));
    const observer = FakeIntersectionObserver.instances.at(-1)!;

    await user.click(screen.getByRole("button", { name: "Outline" }));
    expect(observer.disconnected).toBe(true);
  });
});
