import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { buildSandboxedHtmlDocument, HtmlNodeView, type HtmlFlowNode } from "./HtmlNodeView";

const EXACT_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'; form-action 'none'";
const EXACT_CSP_META = `<meta http-equiv="Content-Security-Policy" content="${EXACT_CSP}">`;

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderHtmlNode(overrides: Partial<HtmlFlowNode["data"]> = {}) {
  const onToggleCollapse = vi.fn();
  const onDelete = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      htmlContent: "<p>hello</p>",
      isCollapsed: false,
      onToggleCollapse,
      onDelete,
      ...overrides,
    },
  } as unknown as NodeProps<HtmlFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <HtmlNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDelete, container };
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
      `<!DOCTYPE html><html><head>${EXACT_CSP_META}</head><body>\n<p>hi</p>\n</body></html>`,
    );
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
    // meta tag, and the attacker's entire string appears intact and
    // unmodified inside the body position.
    expect(result.startsWith(`<!DOCTYPE html><html><head>${EXACT_CSP_META}</head><body>\n`)).toBe(true);
    expect(result.endsWith(`${attacker}\n</body></html>`)).toBe(true);
    expect(result).toBe(`<!DOCTYPE html><html><head>${EXACT_CSP_META}</head><body>\n${attacker}\n</body></html>`);
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

    // Our wrapper's own head/CSP is still the first thing in the document.
    expect(srcDoc.startsWith(`<!DOCTYPE html><html><head>${EXACT_CSP_META}</head><body>\n`)).toBe(true);
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
    expect(srcDoc).toBe(`<!DOCTYPE html><html><head>${EXACT_CSP_META}</head><body>\n${attacker}\n</body></html>`);
  });

  it("the Popout button is present, disabled, and wired to nothing observable", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const user = userEvent.setup();
    renderHtmlNode();

    const popout = screen.getByRole("button", { name: "Popout" });
    expect(popout).toBeDisabled();
    expect(popout).toHaveAttribute(
      "title",
      "Popout view isn't built yet - see the R3 plan doc for why a naive window.open() would be unsafe for untrusted HTML",
    );

    await user.click(popout); // disabled - fires nothing
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
