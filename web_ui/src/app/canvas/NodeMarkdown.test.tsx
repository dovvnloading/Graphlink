import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { CanvasSearchProvider, useSetCanvasSearchQuery } from "./CanvasSearchContext";
import { NodeMarkdown } from "./NodeMarkdown";

// Node redesign, stage 1 ("shared node markdown renderer"): a code-block
// copy button + language badge, a wide-table scroll wrapper, image zoom,
// GitHub-style callouts, and safe external links - shared by every
// text-bearing node kind. See NodeMarkdown.tsx's own doc comment for why
// heading anchors (real in DocumentViewMarkdown.tsx) are deliberately NOT
// here - search-match highlighting WAS in that same "deliberately not here"
// list until ADR-012 stage 12.5, which added it (see the describe block
// below and NodeMarkdown.tsx's own updated doc).

describe("NodeMarkdown", () => {
  it("renders plain markdown content", () => {
    render(<NodeMarkdown content={"# Heading\n\nA paragraph."} />);
    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
    expect(screen.getByText("A paragraph.")).toBeInTheDocument();
  });

  it("does not assign an id to headings (no ToC, and ids would collide across simultaneously-mounted node cards)", () => {
    render(<NodeMarkdown content="## My Section" />);
    const heading = screen.getByRole("heading", { name: "My Section" });
    expect(heading).not.toHaveAttribute("id");
  });

  describe("external link hardening", () => {
    it("adds target=_blank and a safe rel to an absolute http(s) link", () => {
      render(<NodeMarkdown content="[external](https://example.com/page)" />);
      const link = screen.getByRole("link", { name: "external" });
      expect(link).toHaveAttribute("target", "_blank");
      expect(link?.getAttribute("rel")).toContain("noopener");
      expect(link?.getAttribute("rel")).toContain("noreferrer");
      expect(link?.getAttribute("rel")).toContain("nofollow");
    });

  });

  describe("SafeAnchor - only an absolute http(s) href survives as a real link", () => {
    // This content is routinely LLM-generated (chat responses, web-research
    // answers, generated notes) and so is exposed to prompt-injection-driven
    // malicious markdown - a rendered `javascript:` href is a real,
    // browser-executed risk, not a theoretical one. NodeMarkdown assigns no
    // heading ids (see its own doc comment), so a same-node `#fragment`
    // link could never resolve to anything anyway - stripping it is not a
    // functional regression, and mirrors WebResearchNodeView's own
    // already-shipped, already-proven http(s)-only allowlist verbatim
    // rather than loosening it.
    it("strips the href entirely for a javascript: link, rendering only the plain text", () => {
      const { container } = render(<NodeMarkdown content="[click me](javascript:alert(1))" />);
      expect(screen.getByText("click me")).toBeInTheDocument();
      expect(container.querySelector("a")).toBeNull();
    });

    it("strips the href entirely for a file: link", () => {
      const { container } = render(<NodeMarkdown content="[open](file:///etc/passwd)" />);
      expect(screen.getByText("open")).toBeInTheDocument();
      expect(container.querySelector("a")).toBeNull();
    });

    it("strips the href entirely for a data: link", () => {
      const { container } = render(<NodeMarkdown content="[data](data:text/html,<script>alert(1)</script>)" />);
      expect(screen.getByText("data")).toBeInTheDocument();
      expect(container.querySelector("a")).toBeNull();
    });

    it("also strips a same-node fragment link (dead by construction - no heading ids exist to jump to)", () => {
      const { container } = render(<NodeMarkdown content="[jump](#somewhere)" />);
      expect(screen.getByText("jump")).toBeInTheDocument();
      expect(container.querySelector("a")).toBeNull();
    });

    it("still renders a real https link as a genuine, hardened anchor", () => {
      render(<NodeMarkdown content="[real](https://example.com)" />);
      const link = screen.getByRole("link", { name: "real" });
      expect(link).toHaveAttribute("href", "https://example.com");
      expect(link).toHaveAttribute("target", "_blank");
    });

    // Regression tests: adversarial review found that an earlier draft split
    // the security decision (SafeAnchor's own case-insensitive scheme check)
    // from the hardening attributes (previously supplied by a SEPARATE,
    // case-sensitive rehype-external-links pass) - a mixed-case scheme like
    // "HTTPS://..." passed SafeAnchor's check but was invisible to the other
    // plugin's exact-lowercase match, so it rendered as a real, live,
    // same-tab link with no target/rel at all. SafeAnchor now supplies its
    // own target/rel directly, so there is exactly one scheme check and its
    // result determines both whether the link renders AND how hardened it
    // is - these lock that in.
    it("strips the href for a mixed/upper-case dangerous scheme, exactly like the lowercase form", () => {
      const { container } = render(<NodeMarkdown content="[click me](JavaScript:alert(1))" />);
      expect(screen.getByText("click me")).toBeInTheDocument();
      expect(container.querySelector("a")).toBeNull();
    });

    it("still fully hardens a mixed/upper-case http(s) scheme (no silent downgrade to an unhardened link)", () => {
      render(<NodeMarkdown content="[real](HTTPS://example.com)" />);
      const link = screen.getByRole("link", { name: "real" });
      expect(link).toHaveAttribute("href", "HTTPS://example.com");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
      expect(link.getAttribute("rel")).toContain("noreferrer");
    });

    it("fully hardens a bare-URL GFM autolink (no [text](url) brackets) the same as a bracketed link", () => {
      render(<NodeMarkdown content="See https://example.com for details." />);
      const link = screen.getByRole("link", { name: "https://example.com" });
      expect(link).toHaveAttribute("href", "https://example.com");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
    });
  });

  describe("code blocks", () => {
    function mockClipboard(): ReturnType<typeof vi.fn> {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText },
        configurable: true,
        writable: true,
      });
      return writeText;
    }

    it("renders a language badge for a fenced code block", () => {
      render(<NodeMarkdown content={"```typescript\nconst x = 1;\n```"} />);
      expect(screen.getByText("typescript")).toBeInTheDocument();
    });

    it("falls back to a plain 'text' badge when no language is given", () => {
      render(<NodeMarkdown content={"```\nplain block\n```"} />);
      expect(screen.getByText("text")).toBeInTheDocument();
    });

    it("copies the code block's raw text and flashes 'Copied'", async () => {
      const user = userEvent.setup();
      const writeText = mockClipboard();
      render(<NodeMarkdown content={"```python\nprint('hi')\n```"} />);

      await user.click(screen.getByRole("button", { name: "Copy code" }));

      expect(writeText).toHaveBeenCalledWith("print('hi')\n");
      expect(await screen.findByText("Copied")).toBeInTheDocument();
    });

    it("marks the copy button nodrag, so clicking it inside a React Flow node doesn't start a canvas drag", () => {
      render(<NodeMarkdown content={"```\nx\n```"} />);
      expect(screen.getByRole("button", { name: "Copy code" })).toHaveClass("nodrag");
    });
  });

  describe("tables", () => {
    it("wraps a table in a horizontally-scrollable container", () => {
      const table = "| A | B |\n| - | - |\n| 1 | 2 |";
      const { container } = render(<NodeMarkdown content={table} />);
      const wrapper = container.querySelector(".node-md-table-wrapper");
      expect(wrapper).not.toBeNull();
      expect(wrapper?.querySelector("table")).not.toBeNull();
    });
  });

  describe("images", () => {
    it("wraps an image in the zoom component, preserving its src/alt", () => {
      const { container } = render(<NodeMarkdown content="![a diagram](https://example.com/diagram.png)" />);
      expect(container.querySelector("[data-rmiz]")).not.toBeNull();
      const img = screen.getByAltText("a diagram") as HTMLImageElement;
      expect(img.src).toBe("https://example.com/diagram.png");
    });

    it("sets referrerPolicy=no-referrer on a legitimate http(s) image (defense-in-depth alongside the img-src CSP)", () => {
      render(<NodeMarkdown content="![a diagram](https://example.com/diagram.png)" />);
      const img = screen.getByAltText("a diagram") as HTMLImageElement;
      expect(img.getAttribute("referrerpolicy")).toBe("no-referrer");
    });

    // markdown-image-exfil: unlike a link, the browser fetches an <img src>
    // automatically at render time - no click required - so LLM/web-
    // authored markdown (every node kind's content, not just WebResearch)
    // that a prompt injection steers into emitting a dangerous-scheme
    // image src is a live risk the moment the node renders. Mirrors
    // SafeAnchor's own javascript:/file:/data: hardening tests above,
    // applied to the img path instead of the link path.
    it("renders nothing for a javascript: image src", () => {
      const { container } = render(<NodeMarkdown content="![x](javascript:alert(1))" />);
      expect(container.querySelector("img")).toBeNull();
      expect(container.querySelector("[data-rmiz]")).toBeNull();
    });

    it("renders nothing for a data: image src", () => {
      const { container } = render(
        <NodeMarkdown content="![x](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)" />,
      );
      expect(container.querySelector("img")).toBeNull();
    });

    it("renders nothing for a file: image src", () => {
      const { container } = render(<NodeMarkdown content="![x](file:///etc/passwd)" />);
      expect(container.querySelector("img")).toBeNull();
    });

    it("renders nothing for a mixed/upper-case dangerous scheme, exactly like the lowercase form", () => {
      const { container } = render(<NodeMarkdown content="![x](JavaScript:alert(1))" />);
      expect(container.querySelector("img")).toBeNull();
    });
  });

  describe("LaTeX math (node redesign, stage 4)", () => {
    it("renders inline math ($...$) as a KaTeX span", () => {
      const { container } = render(<NodeMarkdown content="The area is $x^2$ square units." />);
      expect(container.querySelector(".katex")).not.toBeNull();
      expect(container.querySelector(".katex-display")).toBeNull();
    });

    it("renders block math ($$...$$ on its own lines) as a KaTeX display block", () => {
      const { container } = render(<NodeMarkdown content={"$$\n\\int_0^1 x^2\\,dx\n$$"} />);
      expect(container.querySelector(".katex-display")).not.toBeNull();
    });

    it("does not crash on malformed LaTeX - renders KaTeX's own inline error instead of throwing", () => {
      const { container } = render(<NodeMarkdown content="Broken: $\\frac{1}{$" />);
      expect(container.querySelector(".katex-error")).not.toBeNull();
    });

    it("leaves an ordinary dollar amount alone (not treated as math)", () => {
      // remark-math requires the closing delimiter on non-whitespace content
      // to trigger - a lone "$5" with no matching close is exactly the kind
      // of prose (chat/note content routinely mentions prices) that must
      // not get swallowed into a broken inline-math parse.
      render(<NodeMarkdown content="It costs $5 and that's final." />);
      expect(screen.getByText(/It costs \$5 and that's final\./)).toBeInTheDocument();
    });
  });

  describe("GitHub-style callouts", () => {
    it("renders a > [!NOTE] blockquote as a styled alert with the NOTE title", () => {
      const { container } = render(<NodeMarkdown content={"> [!NOTE]\n> Useful information."} />);
      const alert = container.querySelector(".markdown-alert-note");
      expect(alert).not.toBeNull();
      expect(alert).toHaveTextContent("NOTE");
      expect(alert).toHaveTextContent("Useful information.");
    });

    it("renders each of the 5 alert types with its own distinct class", () => {
      const content = [
        "> [!NOTE]\n> a",
        "> [!TIP]\n> b",
        "> [!IMPORTANT]\n> c",
        "> [!WARNING]\n> d",
        "> [!CAUTION]\n> e",
      ].join("\n\n");
      const { container } = render(<NodeMarkdown content={content} />);
      for (const type of ["note", "tip", "important", "warning", "caution"]) {
        expect(container.querySelector(`.markdown-alert-${type}`)).not.toBeNull();
      }
    });

    it("leaves an ordinary blockquote (no [!TYPE] marker) unstyled as a plain blockquote", () => {
      const { container } = render(<NodeMarkdown content="> just a quote, not an alert" />);
      expect(container.querySelector(".markdown-alert")).toBeNull();
      expect(container.querySelector("blockquote")).not.toBeNull();
    });
  });
});

function QuerySetter({ query }: { query: string }) {
  const setQuery = useSetCanvasSearchQuery();
  useEffect(() => setQuery(query), [query, setQuery]);
  return null;
}

function renderWithQuery(content: string, query: string) {
  return render(
    <CanvasSearchProvider>
      <QuerySetter query={query} />
      <NodeMarkdown content={content} />
    </CanvasSearchProvider>,
  );
}

describe("NodeMarkdown search-match highlighting (ADR-012 stage 12.5)", () => {
  it("renders plain content with no <mark> when there is no active search query", () => {
    const { container } = render(
      <CanvasSearchProvider>
        <NodeMarkdown content="hello world" />
      </CanvasSearchProvider>,
    );
    expect(container.querySelector("mark")).toBeNull();
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("renders plain content with no <mark> outside any CanvasSearchProvider", () => {
    const { container } = render(<NodeMarkdown content="hello world" />);
    expect(container.querySelector("mark")).toBeNull();
  });

  it("wraps every case-insensitive match of the live search query in a <mark>", () => {
    const { container } = renderWithQuery("hello world, HELLO again", "hello");
    const marks = container.querySelectorAll("mark.document-view-search-match");
    expect(marks).toHaveLength(2);
    expect(marks[0]).toHaveTextContent("hello");
    expect(marks[1]).toHaveTextContent("HELLO");
  });

  it("re-renders highlighting when the query changes", () => {
    const { container, rerender } = render(
      <CanvasSearchProvider>
        <QuerySetter query="world" />
        <NodeMarkdown content="hello world" />
      </CanvasSearchProvider>,
    );
    expect(container.querySelectorAll("mark")).toHaveLength(1);

    rerender(
      <CanvasSearchProvider>
        <QuerySetter query="zzz" />
        <NodeMarkdown content="hello world" />
      </CanvasSearchProvider>,
    );
    expect(container.querySelectorAll("mark")).toHaveLength(0);
  });
});
