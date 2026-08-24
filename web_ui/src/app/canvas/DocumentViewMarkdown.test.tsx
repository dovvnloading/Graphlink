import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DocumentViewMarkdown } from "./DocumentViewMarkdown";

// Document View full redesign, stage 1 ("content rendering upgrades"):
// heading anchors (rehype-slug + rehype-autolink-headings), safe external
// links (rehype-external-links), a code-block copy button + language
// badge, a wide-table scroll wrapper, image zoom, and GitHub-style
// callouts. See DocumentViewMarkdown.tsx's own doc comment for the full
// plugin-pipeline rationale.

describe("DocumentViewMarkdown", () => {
  it("renders plain markdown content", () => {
    render(<DocumentViewMarkdown content={"# Heading\n\nA paragraph."} />);
    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument();
  });

  describe("heading anchors", () => {
    it("assigns a stable slug id to headings", () => {
      render(<DocumentViewMarkdown content="## My Section" />);
      const heading = screen.getByRole("heading", { name: /My Section/ });
      expect(heading).toHaveAttribute("id", "my-section");
    });

    it("appends a hover-revealed anchor link pointing at the heading's own id", () => {
      render(<DocumentViewMarkdown content="## My Section" />);
      const heading = screen.getByRole("heading", { name: /My Section/ });
      const anchor = heading.querySelector(".document-view-heading-anchor");
      expect(anchor).not.toBeNull();
      expect(anchor).toHaveAttribute("href", "#my-section");
      // Decorative - must never intercept keyboard/screen-reader navigation
      // away from the heading's own real text.
      expect(anchor).toHaveAttribute("aria-hidden", "true");
      expect(anchor).toHaveAttribute("tabindex", "-1");
    });
  });

  describe("external link hardening", () => {
    it("adds target=_blank and a safe rel to an absolute http(s) link", () => {
      render(<DocumentViewMarkdown content="[external](https://example.com/page)" />);
      const link = screen.getByRole("link", { name: "external" });
      expect(link).toHaveAttribute("target", "_blank");
      expect(link?.getAttribute("rel")).toContain("noopener");
      expect(link?.getAttribute("rel")).toContain("noreferrer");
      expect(link?.getAttribute("rel")).toContain("nofollow");
    });

    it("does NOT touch a same-document fragment link (must not open a new tab for in-page navigation)", () => {
      render(<DocumentViewMarkdown content="## My Section\n\n[jump](#my-section)" />);
      const link = screen.getByRole("link", { name: "jump" });
      expect(link).not.toHaveAttribute("target");
      expect(link).not.toHaveAttribute("rel");
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
      render(<DocumentViewMarkdown content={"```typescript\nconst x = 1;\n```"} />);
      expect(screen.getByText("typescript")).toBeInTheDocument();
    });

    it("falls back to a plain 'text' badge when no language is given", () => {
      render(<DocumentViewMarkdown content={"```\nplain block\n```"} />);
      expect(screen.getByText("text")).toBeInTheDocument();
    });

    it("copies the code block's raw text (not the syntax-highlighting markup) and flashes 'Copied'", async () => {
      const user = userEvent.setup();
      const writeText = mockClipboard();
      render(<DocumentViewMarkdown content={"```python\nprint('hi')\n```"} />);

      await user.click(screen.getByRole("button", { name: "Copy code" }));

      // A fenced code block's own textContent legitimately ends with a
      // trailing newline (the line before the closing ``` fence) -
      // preserving it exactly (not trimming) is the correct copy-paste
      // behavior, matching what selecting the block's own text would give.
      expect(writeText).toHaveBeenCalledWith("print('hi')\n");
      expect(await screen.findByText("Copied")).toBeInTheDocument();
    });
  });

  describe("tables", () => {
    it("wraps a table in a horizontally-scrollable container", () => {
      const table = "| A | B |\n| - | - |\n| 1 | 2 |";
      const { container } = render(<DocumentViewMarkdown content={table} />);
      const wrapper = container.querySelector(".document-view-table-wrapper");
      expect(wrapper).not.toBeNull();
      expect(wrapper?.querySelector("table")).not.toBeNull();
    });
  });

  describe("images", () => {
    it("wraps an image in the zoom component, preserving its src/alt", () => {
      const { container } = render(<DocumentViewMarkdown content="![a diagram](https://example.com/diagram.png)" />);
      expect(container.querySelector("[data-rmiz]")).not.toBeNull();
      const img = screen.getByAltText("a diagram") as HTMLImageElement;
      expect(img.src).toBe("https://example.com/diagram.png");
    });

    it("sets referrerPolicy=no-referrer on a legitimate http(s) image (defense-in-depth alongside the img-src CSP)", () => {
      render(<DocumentViewMarkdown content="![a diagram](https://example.com/diagram.png)" />);
      const img = screen.getByAltText("a diagram") as HTMLImageElement;
      expect(img.getAttribute("referrerpolicy")).toBe("no-referrer");
    });

    // markdown-image-exfil: a hostile saved chat / imported document is
    // this file's own attack vector (see DocumentViewMarkdown.tsx's own
    // updated doc comment on ZoomImage) - mirrors NodeMarkdown.test.tsx's
    // identical coverage for the sibling component.
    it("renders nothing for a javascript: image src", () => {
      const { container } = render(<DocumentViewMarkdown content="![x](javascript:alert(1))" />);
      expect(container.querySelector("img")).toBeNull();
      expect(container.querySelector("[data-rmiz]")).toBeNull();
    });

    it("renders nothing for a data: image src", () => {
      const { container } = render(
        <DocumentViewMarkdown content="![x](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)" />,
      );
      expect(container.querySelector("img")).toBeNull();
    });

    it("renders nothing for a file: image src", () => {
      const { container } = render(<DocumentViewMarkdown content="![x](file:///etc/passwd)" />);
      expect(container.querySelector("img")).toBeNull();
    });
  });

  describe("GitHub-style callouts", () => {
    it("renders a > [!NOTE] blockquote as a styled alert with the NOTE title", () => {
      const { container } = render(
        <DocumentViewMarkdown content={"> [!NOTE]\n> Useful information."} />,
      );
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
      const { container } = render(<DocumentViewMarkdown content={content} />);
      for (const type of ["note", "tip", "important", "warning", "caution"]) {
        expect(container.querySelector(`.markdown-alert-${type}`)).not.toBeNull();
      }
    });

    it("leaves an ordinary blockquote (no [!TYPE] marker) unstyled as a plain blockquote", () => {
      const { container } = render(<DocumentViewMarkdown content="> just a quote, not an alert" />);
      expect(container.querySelector(".markdown-alert")).toBeNull();
      expect(container.querySelector("blockquote")).not.toBeNull();
    });
  });
});
