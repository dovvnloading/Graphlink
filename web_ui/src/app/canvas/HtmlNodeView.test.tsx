import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ADR-011 stage 11.1: wraps the real useLodVisibility so every ACTUAL
// invocation (mount or re-render) is countable - a React.memo bailout skips
// calling HtmlNodeView's function body entirely, so this hook (called
// unconditionally on every real render) never fires during a bailed
// re-render. Same "mock-wrap-and-delegate" technique ImageNodeView.test.tsx
// established.
const lodVisibilityCalls = { count: 0 };

vi.mock("./useLodVisibility", async (importOriginal) => {
  const original = await importOriginal<typeof import("./useLodVisibility")>();
  return {
    ...original,
    useLodVisibility: (...args: Parameters<typeof original.useLodVisibility>) => {
      lodVisibilityCalls.count += 1;
      return original.useLodVisibility(...args);
    },
  };
});

import {
  buildSandboxedHtmlDocument,
  clampSplitterValue,
  HtmlNodeView,
  makeDebouncedSplitterReport,
  type HtmlFlowNode,
} from "./HtmlNodeView";
import { HTML_SPLIT_MAX, HTML_SPLIT_MIN, HTML_SPLIT_TOTAL_PX } from "./canvasConstants";

// SANDBOX_BASE_ORIGIN's exact value, duplicated here (not imported from
// HtmlNodeView.tsx) deliberately - importing the implementation's own
// constant would make these assertions tautological against a value the
// implementation could change without any test noticing.
const SANDBOX_BASE_ORIGIN = "http://sandboxed-html-node.invalid";
const EXACT_CSP =
  `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri ${SANDBOX_BASE_ORIGIN}; frame-src 'none'; object-src 'none'; form-action 'none'`;
const EXACT_CSP_META = `<meta http-equiv="Content-Security-Policy" content="${EXACT_CSP}">`;
const EXACT_BASE_TAG = `<base href="${SANDBOX_BASE_ORIGIN}/">`;

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderHtmlNode(overrides: Partial<HtmlFlowNode["data"]> = {}) {
  const onToggleCollapse = vi.fn();
  const onDelete = vi.fn();
  const onSplitterChange = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      htmlContent: "<p>hello</p>",
      isCollapsed: false,
      htmlSplitterState: null,
      onToggleCollapse,
      onDelete,
      onSplitterChange,
      ...overrides,
    },
  } as unknown as NodeProps<HtmlFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <HtmlNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDelete, onSplitterChange, container };
}

function getSplitter(container: HTMLElement): HTMLDivElement {
  const splitter = container.querySelector(".html-node-splitter");
  expect(splitter).not.toBeNull();
  return splitter as HTMLDivElement;
}

function getIframe(container: HTMLElement): HTMLIFrameElement {
  const iframe = container.querySelector("iframe.html-node-preview");
  expect(iframe).not.toBeNull();
  return iframe as HTMLIFrameElement;
}

function getSourceTextarea(container: HTMLElement): HTMLTextAreaElement {
  const textarea = container.querySelector("textarea.html-node-source");
  expect(textarea).not.toBeNull();
  return textarea as HTMLTextAreaElement;
}

describe("buildSandboxedHtmlDocument", () => {
  it("wraps raw content verbatim in the exact fixed structure with the exact CSP string", () => {
    const result = buildSandboxedHtmlDocument("<p>hi</p>");
    expect(result).toBe(
      `<!DOCTYPE html><html><head>${EXACT_CSP_META}${EXACT_BASE_TAG}</head><body>\n<p>hi</p>\n</body></html>`,
    );
  });

  // html-preview-reads-token-via-baseURI: an iframe[srcdoc] with no <base> of
  // its own inherits the CONTAINER document's base URL, which for this app
  // is http://127.0.0.1:<port>/#token=<token> - readable via
  // document.baseURI regardless of the sandbox attribute (sandbox's missing
  // allow-same-origin governs DOM/storage/network access, not this fallback
  // base URL mechanism). The fixed <base> tag below is what actually closes
  // that leak - it must be present, token-free, and land inside <head>
  // (before any byte of `raw`, which starts in the body position) so it's
  // unconditionally the base every browser sees regardless of what `raw`
  // itself might contain.
  it("sets an inert, token-free <base> tag in <head>, after the CSP meta and before any byte of `raw`", () => {
    const result = buildSandboxedHtmlDocument("<p>hi</p>");

    expect(result).not.toContain("token=");
    expect(result).not.toContain("127.0.0.1");

    const baseIndex = result.indexOf(EXACT_BASE_TAG);
    const metaIndex = result.indexOf(EXACT_CSP_META);
    const headCloseIndex = result.indexOf("</head>");
    const rawIndex = result.indexOf("<p>hi</p>");
    expect(baseIndex).toBeGreaterThan(-1);
    // The CSP meta tag must precede the <base> tag in the parsed document,
    // so the base-uri directive it carries is already the active policy by
    // the time the parser reaches our <base> element and validates its href
    // against it - a <base> parsed before any CSP meta would have nothing
    // to validate against yet.
    expect(metaIndex).toBeLessThan(baseIndex);
    expect(baseIndex).toBeLessThan(headCloseIndex);
    expect(headCloseIndex).toBeLessThan(rawIndex);

    // Parse the produced string as a real Document (DOMParser, available in
    // jsdom) and assert on the parsed structure rather than only the raw
    // string - a genuine proof that this is a real, single <base> element
    // with the expected href, not e.g. text that merely looks like one
    // inside an attribute value.
    const doc = new DOMParser().parseFromString(result, "text/html");
    const baseEls = doc.querySelectorAll("base");
    expect(baseEls.length).toBe(1);
    expect(baseEls[0].getAttribute("href")).toBe(`${SANDBOX_BASE_ORIGIN}/`);
    expect(baseEls[0].getAttribute("href")).not.toContain("token=");
  });

  it("never branches on adversarial content trying to inject a competing head/CSP", () => {
    const attacker =
      '</body></html><html><head><meta http-equiv="Content-Security-Policy" content="script-src *"></head><body>' +
      "<script>evil()</script>";
    const result = buildSandboxedHtmlDocument(attacker);

    // Our CSP meta tag is still the very first meta tag in the document,
    // positioned before any byte of the attacker's payload - not merged
    // with, replaced by, or reordered around the attacker's own competing
    // head/meta content. Assert exact indices, not mere substring presence.
    const ourMetaIndex = result.indexOf(EXACT_CSP_META);
    const attackerMetaIndex = result.indexOf('content="script-src *"');
    expect(ourMetaIndex).toBeGreaterThan(-1);
    expect(attackerMetaIndex).toBeGreaterThan(-1);
    expect(ourMetaIndex).toBeLessThan(attackerMetaIndex);

    // The wrapper's own head/body scaffolding is untouched: exactly one
    // <!DOCTYPE html>, exactly one wrapper <head>...</head> holding only our
    // meta tag and our <base> tag, and the attacker's entire string appears
    // intact and unmodified inside the body position.
    expect(result.startsWith(`<!DOCTYPE html><html><head>${EXACT_CSP_META}${EXACT_BASE_TAG}</head><body>\n`)).toBe(
      true,
    );
    expect(result.endsWith(`${attacker}\n</body></html>`)).toBe(true);
    expect(result).toBe(
      `<!DOCTYPE html><html><head>${EXACT_CSP_META}${EXACT_BASE_TAG}</head><body>\n${attacker}\n</body></html>`,
    );
  });

  // html-preview-reads-token-via-baseURI: per the HTML spec, only the FIRST
  // <base> element with an href in tree order ever takes effect - later ones
  // are inert. Proves that property holds even when `raw` supplies its own
  // competing <base> tag pointed at an attacker-chosen href: ours (in the
  // wrapper's own <head>, unconditionally before `raw`) must still be the
  // one a browser would honor, not the attacker's.
  it("a competing <base> tag inside `raw` never precedes or replaces ours - ours stays first in tree order", () => {
    const attacker = '<base href="https://attacker.example/">';
    const result = buildSandboxedHtmlDocument(attacker);

    const doc = new DOMParser().parseFromString(result, "text/html");
    const baseEls = doc.querySelectorAll("base");
    expect(baseEls.length).toBe(2);
    // Ours is first in tree order - the one the HTML spec's "frozen base
    // url" algorithm actually uses - regardless of what `raw` supplies.
    expect(baseEls[0].getAttribute("href")).toBe(`${SANDBOX_BASE_ORIGIN}/`);
    expect(baseEls[1].getAttribute("href")).toBe("https://attacker.example/");
    expect(result.indexOf(EXACT_BASE_TAG)).toBeLessThan(result.indexOf(attacker));
  });
});

describe("HtmlNodeView", () => {
  it("typing in the source textarea does NOT change the iframe's srcdoc at all", () => {
    const { container } = renderHtmlNode({ htmlContent: "<p>original</p>" });
    const iframe = getIframe(container);
    const initialSrcDoc = iframe.srcdoc;
    expect(initialSrcDoc).toBe(buildSandboxedHtmlDocument("<p>original</p>"));

    const textarea = getSourceTextarea(container);
    fireEvent.change(textarea, { target: { value: "<script>alert(1)</script>" } });

    expect(textarea.value).toBe("<script>alert(1)</script>");
    // The iframe must be byte-for-byte unchanged after typing.
    expect(getIframe(container).srcdoc).toBe(initialSrcDoc);
  });

  it("clicking Render updates the iframe to reflect the current textarea value, wrapped correctly", async () => {
    const user = userEvent.setup();
    const { container } = renderHtmlNode({ htmlContent: "<p>original</p>" });
    const textarea = getSourceTextarea(container);

    fireEvent.change(textarea, { target: { value: "<p>updated</p>" } });
    expect(getIframe(container).srcdoc).toBe(buildSandboxedHtmlDocument("<p>original</p>")); // still unchanged pre-Render

    await user.click(screen.getByRole("button", { name: "Render" }));

    expect(getIframe(container).srcdoc).toBe(buildSandboxedHtmlDocument("<p>updated</p>"));
  });

  it("the iframe's sandbox attribute is EXACTLY 'allow-scripts', nothing more", () => {
    const { container } = renderHtmlNode();
    const iframe = getIframe(container);
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("the rendered srcdoc contains the exact CSP meta tag string, verbatim", () => {
    const { container } = renderHtmlNode({ htmlContent: "<p>x</p>" });
    const iframe = getIframe(container);
    expect(iframe.srcdoc).toContain(EXACT_CSP_META);
    expect(iframe.srcdoc.indexOf(EXACT_CSP_META)).toBe(iframe.srcdoc.indexOf("<meta"));
  });

  it("adversarial: content containing <head>/</head>/<html>/</html>/a competing CSP meta tag never displaces our CSP or breaks the wrapper", async () => {
    const user = userEvent.setup();
    const attacker =
      "</head></html><html><head>" +
      '<meta http-equiv="Content-Security-Policy" content="script-src *">' +
      "<title>hijacked</title></head><body><h1>pwned</h1>";
    const { container } = renderHtmlNode({ htmlContent: "" });
    const textarea = getSourceTextarea(container);

    fireEvent.change(textarea, { target: { value: attacker } });
    await user.click(screen.getByRole("button", { name: "Render" }));

    const srcDoc = getIframe(container).srcdoc;

    // Our wrapper's own head/CSP/base is still the first thing in the document.
    expect(srcDoc.startsWith(`<!DOCTYPE html><html><head>${EXACT_CSP_META}${EXACT_BASE_TAG}</head><body>\n`)).toBe(
      true,
    );
    const ourMetaIndex = srcDoc.indexOf(EXACT_CSP_META);
    const attackerMetaIndex = srcDoc.indexOf('content="script-src *"');
    expect(ourMetaIndex).toBe(0 + "<!DOCTYPE html><html><head>".length);
    expect(attackerMetaIndex).toBeGreaterThan(-1);
    expect(ourMetaIndex).toBeLessThan(attackerMetaIndex);

    // Only ONE occurrence of our exact CSP meta tag exists (it wasn't
    // duplicated, and the attacker's competing one - a different string,
    // "script-src *" not our policy - doesn't collide with it).
    const occurrences = srcDoc.split(EXACT_CSP_META).length - 1;
    expect(occurrences).toBe(1);

    // The attacker's payload made it through unparsed/unmodified, verbatim,
    // entirely within the body position after our fixed prefix.
    expect(srcDoc).toBe(
      `<!DOCTYPE html><html><head>${EXACT_CSP_META}${EXACT_BASE_TAG}</head><body>\n${attacker}\n</body></html>`,
    );
  });

  it("renders no Popout control, and never opens a window", () => {
    // It used to ship as a permanently disabled button explaining itself in
    // a tooltip. A control the user can never act on is noise on the card;
    // the reason popout is unbuilt lives in the module doc now. Asserted as
    // absence rather than deleted outright, so re-adding a dead placeholder
    // fails here.
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    renderHtmlNode();

    expect(screen.queryByRole("button", { name: "Popout" })).toBeNull();
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it("Collapse/Expand toggle calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const { onToggleCollapse } = renderHtmlNode({ isCollapsed: false });
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("shows 'Expand' label when isCollapsed is true", () => {
    renderHtmlNode({ isCollapsed: true });
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });

  it("Delete calls onDelete", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderHtmlNode();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("collapse hides the source/preview area entirely", () => {
    const { container } = renderHtmlNode({ isCollapsed: true });
    expect(container.querySelector("textarea.html-node-source")).toBeNull();
    expect(container.querySelector("iframe.html-node-preview")).toBeNull();
    expect(screen.queryByRole("button", { name: "Render" })).toBeNull();
  });
});

// R6.3: HTML node splitter-position scaffolding.
describe("HtmlNodeView splitter (R6.3)", () => {
  it("defaults to a 50/50 split (HTML_SPLIT_TOTAL_PX/2 each pane) when htmlSplitterState is null", () => {
    const { container } = renderHtmlNode({ htmlSplitterState: null });
    const textarea = getSourceTextarea(container);
    const iframe = getIframe(container);
    expect(textarea.style.height).toBe(`${HTML_SPLIT_TOTAL_PX / 2}px`);
    expect(iframe.style.height).toBe(`${HTML_SPLIT_TOTAL_PX / 2}px`);
  });

  it("restores a previously saved split position on mount, and the two panes always sum to HTML_SPLIT_TOTAL_PX", () => {
    const { container } = renderHtmlNode({ htmlSplitterState: 0.3 });
    const textarea = getSourceTextarea(container);
    const iframe = getIframe(container);
    expect(textarea.style.height).toBe(`${Math.round(0.3 * HTML_SPLIT_TOTAL_PX)}px`);
    expect(iframe.style.height).toBe(`${HTML_SPLIT_TOTAL_PX - Math.round(0.3 * HTML_SPLIT_TOTAL_PX)}px`);
  });

  it("the splitter is a real separator element wired to a pointerdown handler", () => {
    const { container } = renderHtmlNode();
    const splitter = getSplitter(container);
    expect(splitter).toHaveAttribute("role", "separator");
    expect(splitter).toHaveAttribute("aria-orientation", "horizontal");
  });

  it("dragging the splitter updates the pane heights immediately, then reports the settled value once debounceMs elapses", () => {
    vi.useFakeTimers();
    try {
      const { container, onSplitterChange } = renderHtmlNode({ htmlSplitterState: 0.5 });
      const splitter = getSplitter(container);

      fireEvent.pointerDown(splitter, { clientY: 100 });
      // Dragging DOWN by 28px (10% of HTML_SPLIT_TOTAL_PX=280) grows the
      // Source pane's fraction from 0.5 to 0.6 - visible immediately, no
      // debounce on the live drag itself.
      fireEvent.pointerMove(window, { clientY: 128 });
      const textarea = getSourceTextarea(container);
      expect(textarea.style.height).toBe(`${Math.round(0.6 * HTML_SPLIT_TOTAL_PX)}px`);
      expect(onSplitterChange).not.toHaveBeenCalled();

      fireEvent.pointerUp(window);
      expect(onSplitterChange).not.toHaveBeenCalled(); // still debouncing
      vi.advanceTimersByTime(200);
      expect(onSplitterChange).toHaveBeenCalledOnce();
      expect(onSplitterChange).toHaveBeenCalledWith(0.6);
    } finally {
      vi.useRealTimers();
    }
  });

  it("clamps a far drag to HTML_SPLIT_MAX rather than letting the Preview pane collapse to nothing", () => {
    vi.useFakeTimers();
    try {
      const { container, onSplitterChange } = renderHtmlNode({ htmlSplitterState: 0.5 });
      const splitter = getSplitter(container);

      fireEvent.pointerDown(splitter, { clientY: 0 });
      fireEvent.pointerMove(window, { clientY: 10000 }); // absurdly far down
      fireEvent.pointerUp(window);
      vi.advanceTimersByTime(200);

      expect(onSplitterChange).toHaveBeenCalledWith(HTML_SPLIT_MAX);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a pointerdown/pointerup with no movement in between reports nothing (not even the unchanged value)", () => {
    vi.useFakeTimers();
    try {
      const { container, onSplitterChange } = renderHtmlNode({ htmlSplitterState: 0.5 });
      const splitter = getSplitter(container);

      fireEvent.pointerDown(splitter, { clientY: 50 });
      fireEvent.pointerUp(window);
      vi.advanceTimersByTime(500);

      expect(onSplitterChange).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("clampSplitterValue", () => {
  it("passes values already inside [HTML_SPLIT_MIN, HTML_SPLIT_MAX] through unchanged", () => {
    expect(clampSplitterValue(0.5)).toBe(0.5);
  });

  it("clamps below HTML_SPLIT_MIN up to HTML_SPLIT_MIN", () => {
    expect(clampSplitterValue(-1)).toBe(HTML_SPLIT_MIN);
  });

  it("clamps above HTML_SPLIT_MAX down to HTML_SPLIT_MAX", () => {
    expect(clampSplitterValue(2)).toBe(HTML_SPLIT_MAX);
  });
});

describe("makeDebouncedSplitterReport", () => {
  it("does not call onSplitterChange until debounceMs have elapsed with no further calls", () => {
    vi.useFakeTimers();
    try {
      const onSplitterChange = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedSplitterReport(timerRef, onSplitterChange, 200);

      debounced(0.4);
      expect(onSplitterChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(199);
      expect(onSplitterChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(onSplitterChange).toHaveBeenCalledOnce();
      expect(onSplitterChange).toHaveBeenCalledWith(0.4);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a call before the debounce window elapses cancels the previous one - only the LAST value fires", () => {
    vi.useFakeTimers();
    try {
      const onSplitterChange = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedSplitterReport(timerRef, onSplitterChange, 200);

      debounced(0.4);
      vi.advanceTimersByTime(150);
      debounced(0.6);
      vi.advanceTimersByTime(150);
      expect(onSplitterChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(50);
      expect(onSplitterChange).toHaveBeenCalledOnce();
      expect(onSplitterChange).toHaveBeenCalledWith(0.6);
    } finally {
      vi.useRealTimers();
    }
  });
});

// ADR-011 stage 11.1: React.memo comparator correctness. `lodVisibilityCalls`
// (see the mock above) fires exactly once per ACTUAL render of this view -
// never on a bailed-out one - so it's the oracle for both directions: it must
// stay flat across an irrelevant/equivalent prop change (too-tight would fail
// this) and must increment on a change to a prop the view actually reads
// (too-loose would fail this).
describe("HtmlNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    lodVisibilityCalls.count = 0;
  });

  function baseHtmlProps(overrides: Partial<HtmlFlowNode["data"]> = {}) {
    const data = {
      htmlContent: "<p>hello</p>",
      isCollapsed: false,
      htmlSplitterState: null,
      onToggleCollapse: vi.fn(),
      onDelete: vi.fn(),
      onSplitterChange: vi.fn(),
      ...overrides,
    };
    return { id: "n0", selected: false, data } as unknown as NodeProps<HtmlFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = baseHtmlProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <HtmlNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    // A brand-new object, same primitives and same callback references -
    // exactly what toFlowNodes may mint on an unrelated snapshot. A naive
    // `data === nextData` reference compare (or React.memo's default shallow
    // props compare) would wrongly re-render here.
    const sameValuesNewObject = { ...props.data };
    rerender(
      <ReactFlowProvider>
        <HtmlNodeView {...{ ...props, data: sameValuesNewObject }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("skips re-rendering when only `htmlContent`/`htmlSplitterState` (seed-once fields this view never re-reads) change", () => {
    // Pins the deliberate omission documented on htmlNodeDataAreEqual: both
    // fields only seed local state via a useState initializer on mount and
    // are never read again by any JSX/effect/closure - so a change to either
    // alone must never re-render (and, per the "typing does NOT change the
    // iframe" test above, could not visibly affect anything even if it did).
    const props = baseHtmlProps({ htmlContent: "<p>original</p>", htmlSplitterState: 0.5 });
    const { rerender } = render(
      <ReactFlowProvider>
        <HtmlNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <HtmlNodeView
          {...{ ...props, data: { ...props.data, htmlContent: "<p>different</p>", htmlSplitterState: 0.2 } }}
        />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("re-renders when `isCollapsed` (a field the view reads) changes", () => {
    const props = baseHtmlProps({ isCollapsed: false });
    const { rerender } = render(
      <ReactFlowProvider>
        <HtmlNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <HtmlNodeView {...{ ...props, data: { ...props.data, isCollapsed: true } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });

  it("re-renders when `onSplitterChange` is rebound to a new closure, even though it's never rendered directly in JSX", () => {
    // onSplitterChange is captured fresh into a real pointerdown event-handler
    // closure on every render (onSplitterPointerDown's onPointerUp) - a
    // comparator that only checked fields visible in JSX would miss this and
    // leave a stale closure attached, a genuine "stale UI" bug.
    const props = baseHtmlProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <HtmlNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <HtmlNodeView {...{ ...props, data: { ...props.data, onSplitterChange: vi.fn() } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });
});
