import { useEffect, useRef, useState, type RefObject } from "react";
import type { DocumentHeading } from "./documentViewHeadings";

/**
 * Document View full redesign, stage 2 ("table of contents + reading
 * progress") - the outline toggle + dropdown. A small, self-contained
 * dropdown (own useState + outside-click/Escape handling), not the app's
 * shared overlay registry (`overlays.tsx`'s Popover) and not NodeMenu -
 * DocumentViewPanel.tsx's own doc comment already documents its deliberate
 * choice not to use the overlay system, and NodeMenu's portal-based
 * positioning exists specifically to escape React Flow's transformed
 * coordinate space, which this panel (a plain flex sibling in App.tsx, not
 * an RF node) was never inside in the first place.
 *
 * Auto-hidden entirely when there are fewer than 2 headings - the same
 * threshold Notion's own floating table of contents uses (a single heading
 * isn't worth an outline control at all).
 */
export function DocumentViewToc({
  headings,
  scrollContainerRef,
}: {
  headings: DocumentHeading[];
  scrollContainerRef: RefObject<HTMLDivElement | null>;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const hasEnoughHeadings = headings.length >= 2;

  // Active-section tracking, only while the dropdown is actually open - no
  // point paying for continuous IntersectionObserver callbacks while the
  // user can't even see the highlight. rootMargin shrinks the effective
  // "visible" area to the TOP 30% of the scroll container, so the
  // highlighted section tracks whatever the reader is currently AT (near
  // the top), not merely whatever happens to be anywhere on screen - the
  // same technique behind every "on this page" scrollspy implementation
  // researched for this stage.
  useEffect(() => {
    if (!isOpen || !hasEnoughHeadings) return;
    const root = scrollContainerRef.current;
    if (!root) return;
    const elements = headings
      .map((h) => document.getElementById(h.id))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting);
        if (visible.length === 0) return;
        const topMost = visible.reduce((a, b) =>
          a.boundingClientRect.top <= b.boundingClientRect.top ? a : b,
        );
        setActiveId(topMost.target.id);
      },
      { root, rootMargin: "0px 0px -70% 0px", threshold: 0 },
    );
    for (const el of elements) observer.observe(el);
    return () => observer.disconnect();
  }, [isOpen, hasEnoughHeadings, headings, scrollContainerRef]);

  useEffect(() => {
    if (!isOpen) return;
    function onPointerDown(event: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setIsOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setIsOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [isOpen]);

  function onSelectHeading(id: string) {
    const container = scrollContainerRef.current;
    const target = document.getElementById(id);
    if (container && target) {
      // Manual scrollTop math (not target.scrollIntoView()) keeps the
      // scroll scoped to exactly this container - scrollIntoView can walk
      // up to an OUTER scrollable ancestor in edge cases, which would be
      // wrong here (this panel's own scroll area, not the whole page,
      // should move).
      const containerRect = container.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      container.scrollTop += targetRect.top - containerRect.top;
    }
    setIsOpen(false);
  }

  if (!hasEnoughHeadings) return null;

  return (
    <div className="document-view-toc" ref={rootRef}>
      <button
        type="button"
        className="document-view-toc-toggle"
        aria-haspopup="true"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
        title="Table of contents"
      >
        Outline
      </button>
      {isOpen && (
        <div className="document-view-toc-dropdown" role="menu" aria-label="Table of contents">
          {headings.map((heading) => (
            <button
              key={heading.id}
              type="button"
              role="menuitem"
              className={
                "document-view-toc-item" +
                ` document-view-toc-depth-${Math.min(heading.depth, 3)}` +
                (heading.id === activeId ? " active" : "")
              }
              onClick={() => onSelectHeading(heading.id)}
            >
              {heading.text}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
