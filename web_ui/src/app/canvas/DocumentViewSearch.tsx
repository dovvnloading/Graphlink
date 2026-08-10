import { useCallback, useEffect, useState } from "react";

/**
 * Document View full redesign, stage 3 ("in-document search/find"). A
 * controlled search bar - unlike DocumentViewToc.tsx's mostly self-contained
 * open/active state, the search QUERY itself has to reach a sibling
 * (DocumentViewMarkdown, which highlights matches via
 * rehypeHighlightSearchMatches), so DocumentViewPanel.tsx owns the actual
 * state and this component is a plain controlled input + toolbar.
 *
 * Match counting and current-match navigation are NOT computed here from
 * the raw query/content - they're derived from the rendered DOM (the real
 * <mark> elements DocumentViewMarkdown actually produced) by
 * DocumentViewPanel.tsx, so the "n of m" display can never drift from what's
 * actually highlighted on screen.
 *
 * The visible "n of m" count updates instantly on every keystroke/navigation
 * (no perceptible lag for sighted users), but what's actually announced to
 * screen readers is a SEPARATE, visually-hidden, debounced copy - caught by
 * adversarial review: an aria-live="polite" region on the same instantly-
 * updating text would queue a fresh announcement on every single keystroke
 * of a multi-character query, and most screen readers read a "polite"
 * queue's backlog out in full rather than skipping to the latest value -
 * flooding the user with several stale counts read out well after they've
 * already moved on. Debouncing only the ANNOUNCED copy (not the visible
 * text, and not the actual highlighting/navigation, which both stay
 * instant) keeps the visual experience responsive while giving assistive
 * tech one settled announcement per pause in typing/navigation instead of
 * one per keystroke.
 */
const ANNOUNCE_DEBOUNCE_MS = 400;

export function DocumentViewSearch({
  isOpen,
  query,
  onQueryChange,
  matchCount,
  currentMatchNumber,
  onNext,
  onPrevious,
  onClose,
}: {
  isOpen: boolean;
  query: string;
  onQueryChange: (query: string) => void;
  matchCount: number;
  currentMatchNumber: number;
  onNext: () => void;
  onPrevious: () => void;
  onClose: () => void;
}) {
  const countText = query.trim() ? `${currentMatchNumber} of ${matchCount}` : "";

  // Hooks run unconditionally, before the `isOpen` early return below (Rules
  // of Hooks) - harmless when closed, since DocumentViewPanel resets `query`
  // to "" on close anyway, which this effect just debounces down to "" too.
  const [announcedText, setAnnouncedText] = useState(countText);
  useEffect(() => {
    const timer = setTimeout(() => setAnnouncedText(countText), ANNOUNCE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [countText]);

  // Deliberate autofocus: the input should grab focus the moment the search
  // bar appears, exactly like a browser/editor "Find" bar, so a keyboard
  // user can start typing immediately without an extra Tab. This component
  // itself never unmounts (DocumentViewPanel.tsx always renders it; only the
  // `isOpen` early return below toggles whether the JSX below - including
  // this <input> - exists), so a plain "focus on component mount" effect
  // would only fire the very first time and never again on subsequent
  // reopens. A stable callback ref instead fires exactly when the <input>
  // DOM node itself is created, i.e. every time this bar reopens - matching
  // the JSX `autoFocus` behavior it replaces without tripping
  // jsx-a11y/no-autofocus.
  const focusInputOnMount = useCallback((element: HTMLInputElement | null) => {
    element?.focus();
  }, []);

  if (!isOpen) return null;

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (event.shiftKey) onPrevious();
      else onNext();
    }
  }

  const hasMatches = matchCount > 0;

  return (
    <div className="document-view-search" role="search">
      <input
        type="text"
        className="document-view-search-input"
        placeholder="Find in document"
        aria-label="Search query"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        onKeyDown={onKeyDown}
        ref={focusInputOnMount}
      />
      <span className="document-view-search-count" aria-hidden="true">
        {countText}
      </span>
      <span className="document-view-visually-hidden" aria-live="polite">
        {announcedText}
      </span>
      <button
        type="button"
        className="document-view-search-nav"
        onClick={onPrevious}
        disabled={!hasMatches}
        title="Previous match"
        aria-label="Previous match"
      >
        ↑
      </button>
      <button
        type="button"
        className="document-view-search-nav"
        onClick={onNext}
        disabled={!hasMatches}
        title="Next match"
        aria-label="Next match"
      >
        ↓
      </button>
      <button type="button" className="document-view-search-close" onClick={onClose} title="Close search" aria-label="Close search">
        ×
      </button>
    </div>
  );
}
