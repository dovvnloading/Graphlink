import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DocumentViewMarkdown } from "./DocumentViewMarkdown";
import { DocumentViewToc } from "./DocumentViewToc";
import { DocumentViewSearch } from "./DocumentViewSearch";
import { extractHeadings } from "./documentViewHeadings";

const SEARCH_MATCH_SELECTOR = ".document-view-search-match";
const SEARCH_MATCH_CURRENT_CLASS = "document-view-search-match-current";

const DEFAULT_WIDTH = 500;
const MIN_WIDTH = 320;
const MAX_WIDTH = 900;

// Stage 4: relative multipliers, not absolute pixel values - the actual
// inherited body font-size in `.document-view-panel-scroll` is never set
// explicitly by this file (it cascades down from ambient app chrome), so
// hard-coding a "base" px value here could drift from whatever that
// ambient size actually is. `1` (the default/middle step) omits the inline
// style entirely (see the render below), leaving today's already-shipped,
// already-verified rendering completely undisturbed; only stepping away
// from it applies an explicit `Nem` override, scaled relative to whatever
// the ambient size already was.
const FONT_SIZE_STEPS = [0.85, 1, 1.15, 1.3];
const DEFAULT_FONT_SIZE_STEP_INDEX = 1;

/**
 * Document View panel (Qt-removal plan R7.6b's stub, redone as a real
 * docked panel, then refined further). Legacy's DocumentViewerPanel/
 * DocumentViewerWebHost (graphlink_window.py/graphlink_document_viewer_web.py,
 * deleted in R7.6b) was a permanent embedded QWidget - a fixed-500px,
 * flush-left panel that was a QHBoxLayout SIBLING of the graph view, toggled
 * via setVisible(). This restores that shape (a real docked flex sibling -
 * see App.tsx, which owns open/close state and mounts this alongside every
 * other piece of chrome, not just the canvas) and goes further: a real
 * slide transition (so opening/closing reads as a drawer, not a jump-cut),
 * a drag-to-resize handle, a Copy button, and a source subtitle so a
 * complex graph with many nodes doesn't leave "Document View" as the only
 * clue about which node's content is on screen.
 *
 * The slide animation only ever transitions the OUTER element's width
 * (0 <-> the user's chosen width) while the INNER content is held at a
 * fixed width the whole time - if the inner content's own width tracked the
 * animating outer width, ReactMarkdown's rendered text would reflow line by
 * line for the entire 220ms transition (visibly janky); clipping a
 * constant-width inner block via the outer's overflow:hidden instead reads
 * as a real slide, the same technique most CSS-only drawer components use.
 *
 * Closing it only ever happens via its own Close button OR Escape (added in
 * stage 4 - see below), matching legacy's shape otherwise: it was never part
 * of Qt's OverlayManager, so still no scrim and no focus trap - Escape-to-
 * close was the one specific piece of modal-like behavior worth adding on
 * its own, not a signal that the rest of that treatment is coming too.
 *
 * Full redesign, stage 1 of 4 ("content rendering upgrades"): the markdown
 * body itself is now rendered by DocumentViewMarkdown.tsx (heading anchors,
 * a code-block copy button + language badge, wide-table scroll wrapper,
 * image zoom, GitHub-style callouts) rather than a bare
 * `<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>`
 * - see that component's own doc comment for the full plugin-pipeline
 * rationale.
 *
 * Full redesign, stage 2 of 4 ("table of contents + reading progress"): a
 * DocumentViewToc.tsx outline toggle in the header (self-hidden under 2
 * headings - see its own doc comment) and a thin reading-progress bar
 * (scroll percentage through `.document-view-panel-scroll`, computed here
 * rather than in a separate component since it needs the exact same scroll
 * container the ToC's own scroll-to-heading logic needs a ref to anyway).
 * Both reset - scroll position back to the top, progress back to 0 - the
 * moment `content` changes, so switching from a long document to a
 * different (or shorter) one never starts the reader in the middle of the
 * new content or shows a stale progress percentage before the next scroll
 * event fires.
 *
 * Full redesign, stage 3 of 4 ("in-document search/find"): a "Find" toggle
 * opens DocumentViewSearch.tsx's search bar. The query itself is passed down
 * to DocumentViewMarkdown, which highlights matches declaratively (a
 * rehype plugin, see documentViewSearchHighlight.ts); the match COUNT and
 * which one is "current" are derived back out of the rendered DOM here (the
 * real `<mark>` elements actually produced), rather than computed
 * independently from the query/content, so the "n of m" display can never
 * drift from what's actually highlighted on screen. Both the query and the
 * current-match index reset - the former whenever `content` changes
 * (grouped with stage 2's own content-change reset below), the latter
 * whenever the query itself changes (jumping back to the first match of a
 * newly-typed search, matching standard find-bar behavior) - using the same
 * render-phase "adjust state when a value changes" idiom stage 2 already
 * established, not a useEffect.
 *
 * Full redesign, stage 4 of 4 ("drawer UX polish"), the last of the four:
 *
 * - Escape-to-close: a document-level listener, scoped to `isOpen` (matching
 *   DocumentViewToc.tsx's and DocumentViewSearch.tsx's own precedent for
 *   global-but-scoped key handling in this panel). Defers rather than
 *   closing the whole panel if a more specific, nested transient UI should
 *   consume the keystroke instead: the search bar (checked via
 *   `isSearchOpen`, which this component already owns) or the ToC outline
 *   dropdown (checked via a DOM query for its own class, since that
 *   component owns its open state privately - reading the rendered DOM as
 *   ground truth for "is this open" rather than lifting that state up
 *   matches the same technique this file's own search-match tracking above
 *   already uses `SEARCH_MATCH_SELECTOR` for).
 * - Remembered/resettable width: "remembered" here means for the lifetime of
 *   the running app, not across a restart - this component is never
 *   unmounted while the app runs (App.tsx mounts it once, `isOpen` only
 *   toggles visibility), so `width` already survives every close/reopen for
 *   free. Persisting it across a full app restart would need a new
 *   backend-settings round trip (SettingsManager/backend/settings.py's
 *   established pattern) for a single panel's pixel width - a disproportionate
 *   amount of new backend surface for what this stage is scoped as: a
 *   frontend-only polish pass, matching stages 1-3's own shape. "Resettable"
 *   is a double-click on the resize handle, snapping back to
 *   `DEFAULT_WIDTH` - a common, discoverable convention (e.g. VS Code's own
 *   sidebar resize handle).
 * - Expand/fullscreen toggle: `isExpanded` is independent of `width` - it
 *   doesn't overwrite the user's own chosen/remembered width, it just
 *   temporarily overrides the RENDERED size via a CSS class
 *   (`.document-view-panel-expanded`) instead of the usual inline
 *   `style={{width}}`, so un-expanding snaps right back to the exact width
 *   the user had before. Starting a manual drag while expanded exits
 *   expanded mode first (see onResizeStart) - dragging implies "I want
 *   precise manual control now," and continuing to drag while a CSS class
 *   was fighting the inline width would be visibly broken.
 * - Font-size stepper: relative `em` multipliers, not absolute pixel values -
 *   see FONT_SIZE_STEPS' own comment for why. At the time this stage
 *   shipped, `.chat-node-content` h1-h6 were fixed px values independent of
 *   this stepper (only paragraphs/lists/table cells/blockquotes/code
 *   scaled) - a deliberate, documented limitation, since proportionally
 *   scaling headings too would have meant touching a rule set shared by
 *   every OTHER markdown surface in the app (chat bubbles, notes), a much
 *   larger blast radius than this "polish" pass warranted at the time.
 *   A LATER, separate change (the node redesign's own stage 2, fixing an
 *   unrelated inverted-heading-hierarchy bug in node cards) converted those
 *   same shared h1-h6 rules to `em`, as a deliberate, independent decision
 *   for THAT change's own reasons - which means headings in THIS panel now
 *   scale with this stepper too, incidentally, not because this stage was
 *   revisited. Documented here so this comment doesn't keep asserting an
 *   invariant the shared CSS no longer holds.
 *
 * Width, expanded state, and font-size step are NOT reset when `content`
 * changes (unlike stage 2/3's scroll/progress/search state) - they describe
 * how the user likes the PANEL sized/scaled, not anything about the specific
 * document currently showing in it.
 */
export function DocumentViewPanel({
  isOpen,
  content,
  sourceLabel,
  onClose,
}: {
  isOpen: boolean;
  content: string | null;
  sourceLabel: string | null;
  onClose: () => void;
}) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [fontSizeStepIndex, setFontSizeStepIndex] = useState(DEFAULT_FONT_SIZE_STEP_INDEX);
  const panelRef = useRef<HTMLElement>(null);
  const dragStartRef = useRef<{ pointerX: number; startWidth: number } | null>(null);

  // Real Pointer Capture, not a window-level pointermove/pointerup pair:
  // capturing the pointer on the handle ITSELF guarantees the browser keeps
  // routing every subsequent event for that pointer ID here (even once the
  // cursor leaves the handle's own thin hit-box, or the window entirely)
  // and - critically - guarantees a pointerup/pointercancel eventually
  // fires and auto-releases capture. A manual window listener has no such
  // guarantee: if pointerup is ever missed (an interrupted drag, a
  // synthetic click that never dispatches a real mouseup), isResizing gets
  // stuck true forever with a dangling global listener, and the panel then
  // silently resizes itself in response to ANY future mouse movement
  // anywhere on the page, from any other interaction - a real bug found
  // exactly this way while live-verifying the drawer transition.
  const onResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragStartRef.current = { pointerX: event.clientX, startWidth: width };
      setIsResizing(true);
      // Dragging implies "give me precise manual control now" - this exits
      // expanded mode so onResizeMove's own math (based on `width`'s last-
      // remembered value, not whatever the expanded CSS class currently
      // renders) takes over. Adversarial review correctly flagged an
      // earlier draft of this comment for claiming that transition happens
      // smoothly/gradually - it does not: `setIsResizing`/`setIsExpanded`
      // land in the same React commit, so `.document-view-panel-resizing`'s
      // `transition: none` (styles.css) is already active in the very
      // render where the width also changes, meaning the panel visibly
      // snaps straight to `width` on pointerdown itself, before any actual
      // drag motion. This is deliberately left as an instant snap, not
      // smoothed out: it's the same convention real window managers use for
      // "un-maximize by starting to drag" (an immediate jump to the
      // previous/restored size, not an animated shrink), which users
      // already read as normal, expected behavior rather than a glitch.
      setIsExpanded(false);
      // Feature-detected, not assumed: real browsers have supported Pointer
      // Capture for years, but the DOM environment this runs in (an older
      // WebView2/browser, or a test environment like jsdom) may not.
      event.currentTarget.setPointerCapture?.(event.pointerId);
    },
    [width],
  );

  const onResizeMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const start = dragStartRef.current;
    if (!start) return;
    const next = start.startWidth + (event.clientX - start.pointerX);
    setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, next)));
  }, []);

  // Resets width to the default. If the panel happens to be Expanded when
  // this runs, it's ALSO already been un-expanded by this point - not by
  // this function, but as an unavoidable side effect of onResizeStart
  // (bound to pointerdown) already having fired twice before either a
  // dblclick or this handler's own Enter/Space path ever reaches here (a
  // double-click is, unavoidably, two prior pointerdown/pointerup cycles;
  // the keyboard path calls onResetWidth directly with no such prelude, so
  // it does NOT itself touch `isExpanded`). Left as-is deliberately, not
  // worked around: "reset" reasonably means "back to the normal, default-
  // width, non-expanded state" as a whole, not just the `width` number in
  // isolation.
  const onResetWidth = useCallback(() => {
    setWidth(DEFAULT_WIDTH);
  }, []);

  const onResizeHandleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      // Adversarial-review finding: double-click-to-reset had no keyboard
      // equivalent at all - the handle was a plain, non-focusable div, so a
      // keyboard-only user had no way to invoke it. Enter/Space (not arrow-
      // key resizing, which would be a larger, separate feature) mirrors
      // the double-click's own single action.
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onResetWidth();
      }
    },
    [onResetWidth],
  );

  const onResizeEnd = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    dragStartRef.current = null;
    setIsResizing(false);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    if (!content) return;
    navigator.clipboard?.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [content]);

  // Stage 2: table of contents + reading progress. Extracted from the raw
  // markdown source (not queried from the rendered DOM) - see
  // documentViewHeadings.ts's own doc comment for why this is both simpler
  // and available before the very first paint.
  const headings = useMemo(() => extractHeadings(content ?? ""), [content]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [readingProgress, setReadingProgress] = useState(0);

  const onScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    const scrollable = el.scrollHeight - el.clientHeight;
    setReadingProgress(scrollable > 0 ? Math.min(100, Math.max(0, (el.scrollTop / scrollable) * 100)) : 0);
  }, []);

  // Stage 3: in-document search/find. `searchQuery` is the only piece
  // DocumentViewMarkdown needs (to highlight matches); `currentMatchIndex`
  // and `matchCount` are purely local to navigating those already-rendered
  // matches, resolved against the real DOM below. Declared before the
  // content-change reset block below, which references these setters.
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [matchCount, setMatchCount] = useState(0);

  // A new document (or the panel closing and a different one opening next)
  // must never start the reader mid-scroll from whatever the PREVIOUS
  // document left scrollTop at, and the progress bar must not show a stale
  // percentage until the next real scroll event fires. Split across two
  // mechanisms, each satisfying a different lint rule this project
  // enforces: the `readingProgress` reset uses React's own recommended
  // "adjust state when a prop changes" pattern - a plain conditional
  // during render, not a useEffect, avoiding the extra
  // render-then-effect-then-rerender cascade a `useEffect([content])`
  // calling setState would cause (react-hooks/set-state-in-effect). The
  // scrollTop reset can't join that same conditional - refs may never be
  // read or written during render (react-hooks/refs) - so it stays in its
  // own plain effect below, which itself calls no setState at all.
  //
  // Stage 3's search state resets here too - a different document means the
  // previous search (if any) no longer applies to what's on screen.
  const [lastRenderedContent, setLastRenderedContent] = useState(content);
  if (content !== lastRenderedContent) {
    setLastRenderedContent(content);
    setReadingProgress(0);
    setIsSearchOpen(false);
    setSearchQuery("");
  }

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [content]);

  // A newly-typed query starts back at the first match, matching standard
  // find-bar behavior - the previous query's current-match position has no
  // meaning against a different set of matches.
  const [lastSearchQuery, setLastSearchQuery] = useState(searchQuery);
  if (searchQuery !== lastSearchQuery) {
    setLastSearchQuery(searchQuery);
    setCurrentMatchIndex(0);
  }

  const onSearchNext = useCallback(() => {
    setCurrentMatchIndex((i) => (matchCount === 0 ? 0 : (i + 1) % matchCount));
  }, [matchCount]);

  const onSearchPrevious = useCallback(() => {
    setCurrentMatchIndex((i) => (matchCount === 0 ? 0 : (i - 1 + matchCount) % matchCount));
  }, [matchCount]);

  const onSearchClose = useCallback(() => {
    setIsSearchOpen(false);
    setSearchQuery("");
  }, []);

  // Runs after every render where the highlighted matches could have
  // changed (new content, new query) or the user navigated to a different
  // one - reads the actual <mark> elements DocumentViewMarkdown produced
  // (the source of truth for "how many matches" and "which one is
  // current"), rather than recomputing that independently and risking it
  // drifting from what's really on screen.
  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const allMatches = Array.from(container.querySelectorAll<HTMLElement>(SEARCH_MATCH_SELECTOR));
    setMatchCount(allMatches.length);
    for (const el of allMatches) el.classList.remove(SEARCH_MATCH_CURRENT_CLASS);
    if (allMatches.length === 0) return;

    const current = allMatches[Math.min(currentMatchIndex, allMatches.length - 1)];
    current.classList.add(SEARCH_MATCH_CURRENT_CLASS);
    const containerRect = container.getBoundingClientRect();
    const currentRect = current.getBoundingClientRect();
    container.scrollTop += currentRect.top - containerRect.top;
  }, [content, searchQuery, currentMatchIndex]);

  // Stage 4: Escape-to-close. Deferring to a more specific, nested transient
  // UI - the search bar (this component's own `isSearchOpen`) or the ToC
  // outline dropdown (no state access here, so checked via the rendered DOM
  // instead, scoped to this panel's own root) - means each one still closes
  // on its own first Escape press, exactly as stage 2/3 already established,
  // rather than this new listener closing the ENTIRE panel out from under
  // whichever of those was actually still open.
  //
  // Adversarial-review finding, confirmed and fixed: when both the search
  // bar and the ToC dropdown were open at once, and focus was anywhere OTHER
  // than inside the search input, one Escape press closed BOTH - not just
  // the search bar this branch means to defer to. DocumentViewToc.tsx's own
  // Escape listener is a SEPARATE `document`-level listener that closes
  // itself unconditionally whenever it's open, with no awareness of
  // anything else reacting to the same keystroke; merely calling
  // `onSearchClose()` and returning here does nothing to stop that OTHER
  // listener from also firing on the very same dispatch. Plain
  // `stopPropagation()` would not fix this either - both listeners are
  // attached to the SAME target (`document`), and per the DOM event model
  // `stopPropagation()` only prevents an event from reaching FURTHER
  // targets (e.g. document -> window), not other listeners already
  // registered on the target it's currently at; only
  // `stopImmediatePropagation()` prevents sibling listeners on the same
  // node from running. Calling it here means this branch's "close search,
  // leave everything else alone" intent is actually enforced, regardless of
  // which of this listener's or ToC's own listener happens to be registered
  // first for a given interaction order.
  useEffect(() => {
    if (!isOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (isSearchOpen) {
        onSearchClose();
        event.stopImmediatePropagation();
        return;
      }
      if (panelRef.current?.querySelector(".document-view-toc-dropdown")) return;
      onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isOpen, isSearchOpen, onSearchClose, onClose]);

  const onFontSizeDecrease = useCallback(() => {
    setFontSizeStepIndex((i) => Math.max(0, i - 1));
  }, []);

  const onFontSizeIncrease = useCallback(() => {
    setFontSizeStepIndex((i) => Math.min(FONT_SIZE_STEPS.length - 1, i + 1));
  }, []);

  const fontSizeMultiplier = FONT_SIZE_STEPS[fontSizeStepIndex];

  return (
    <aside
      ref={panelRef}
      className={[
        "document-view-panel",
        isOpen ? "document-view-panel-open" : "",
        isResizing ? "document-view-panel-resizing" : "",
        isExpanded ? "document-view-panel-expanded" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-label="Document View"
      aria-hidden={!isOpen}
      style={{ width: isOpen ? (isExpanded ? undefined : width) : 0 }}
    >
      <div
        className={["document-view-panel-inner", isExpanded ? "document-view-panel-expanded" : ""]
          .filter(Boolean)
          .join(" ")}
        style={{ width: isExpanded ? undefined : width }}
      >
        <header className="document-view-panel-header">
          <div className="document-view-panel-heading">
            <span className="document-view-panel-title">Document View</span>
            {sourceLabel && <span className="document-view-panel-subtitle">{sourceLabel}</span>}
          </div>
          <DocumentViewToc headings={headings} scrollContainerRef={scrollRef} />
          <button
            type="button"
            className="document-view-panel-search-toggle"
            onClick={() => (isSearchOpen ? onSearchClose() : setIsSearchOpen(true))}
            disabled={!content}
            title="Find in document"
            aria-label="Find in document"
            aria-expanded={isSearchOpen}
          >
            Find
          </button>
          <button
            type="button"
            className="document-view-panel-copy"
            onClick={onCopy}
            disabled={!content}
            title="Copy content"
            aria-label="Copy content"
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <button type="button" className="document-view-panel-close" onClick={onClose}>
            Close
          </button>
        </header>
        {/* A separate row, not folded into the header above: with the main
            header already carrying 5 controls (title + Outline/Find/Copy/
            Close), adding these 3 more (font stepper x2, Expand) there too
            would overflow this panel's own supported MIN_WIDTH (320px) -
            confirmed by rough measurement, not just a hunch (~444px of
            fixed-width buttons alone against a ~300px content budget at
            MIN_WIDTH, before even the title). This row has far fewer
            neighbors competing for space. */}
        <div className="document-view-panel-toolbar">
          <div className="document-view-panel-font-stepper">
            <button
              type="button"
              className="document-view-panel-font-step"
              onClick={onFontSizeDecrease}
              disabled={fontSizeStepIndex === 0}
              title="Decrease text size"
              aria-label="Decrease text size"
            >
              A-
            </button>
            <button
              type="button"
              className="document-view-panel-font-step"
              onClick={onFontSizeIncrease}
              disabled={fontSizeStepIndex === FONT_SIZE_STEPS.length - 1}
              title="Increase text size"
              aria-label="Increase text size"
            >
              A+
            </button>
          </div>
          <button
            type="button"
            className="document-view-panel-expand-toggle"
            onClick={() => setIsExpanded((expanded) => !expanded)}
            title={isExpanded ? "Collapse" : "Expand"}
            aria-label={isExpanded ? "Collapse" : "Expand"}
            aria-pressed={isExpanded}
          >
            {isExpanded ? "Collapse" : "Expand"}
          </button>
        </div>
        <DocumentViewSearch
          isOpen={isSearchOpen}
          query={searchQuery}
          onQueryChange={setSearchQuery}
          matchCount={matchCount}
          currentMatchNumber={matchCount === 0 ? 0 : currentMatchIndex + 1}
          onNext={onSearchNext}
          onPrevious={onSearchPrevious}
          onClose={onSearchClose}
        />
        <div
          className="document-view-panel-progress"
          role="progressbar"
          aria-label="Reading progress"
          aria-valuenow={Math.round(readingProgress)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="document-view-panel-progress-fill" style={{ width: `${readingProgress}%` }} />
        </div>
        <div
          className="document-view-panel-scroll chat-node-content"
          ref={scrollRef}
          onScroll={onScroll}
          style={{ fontSize: fontSizeMultiplier === 1 ? undefined : `${fontSizeMultiplier}em` }}
        >
          <DocumentViewMarkdown content={content ?? ""} searchQuery={searchQuery} />
        </div>
      </div>
      {/* This is the ARIA APG "window splitter" pattern: a focusable,
          interactive separator you drag (or keyboard-reset) to resize the
          adjacent pane. It is deliberately NOT role="button" - it has no
          single click-to-activate action, and reporting it as a button
          would misrepresent its semantics to screen reader users, who rely
          on the "separator" role to know this is a movable boundary, not a
          button. jsx-a11y's default role classification doesn't special-
          case this ARIA-legitimate interactive variant of role="separator",
          hence the two scoped disables below rather than a role change. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- interactive separator/splitter widget per ARIA APG, not a button (see comment above) */}
      <div
        className="document-view-panel-resize-handle"
        onPointerDown={onResizeStart}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeEnd}
        onPointerCancel={onResizeEnd}
        onDoubleClick={onResetWidth}
        onKeyDown={onResizeHandleKeyDown}
        role="separator"
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- focusable separator (splitter) per ARIA APG; intentionally role="separator", not role="button"
        tabIndex={0}
        aria-orientation="vertical"
        aria-label="Resize Document View panel. Press Enter to reset to the default width."
        title="Drag to resize, double-click or Enter to reset"
      />
    </aside>
  );
}
