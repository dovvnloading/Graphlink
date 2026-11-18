import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ChatCitationChips, citationsForReply, sectionUrl,
  KNOWLEDGE_CITATION_RECORD,
} from "./ChatCitationChips";
import type { ToolInvocationData } from "./ChatNodeView";

const ID = "kb:0123456789abcdef0123";
const NOT_SENT = "kb:aaaaaaaaaaaaaaaaaaaa";
const EXCERPT = "Restore the latest encrypted snapshot after stopping writers.";

function record(overrides: Record<string, unknown> = {}): ToolInvocationData {
  const source = {
    citationId: ID,
    documentTitle: "Database recovery",
    sourceUri: "https://example.org/ops/recovery.md",
    sourceFile: "https://example.org/ops/recovery.md",
    section: "Restore and verify",
    offsetStart: 18,
    offsetEnd: 80,
    excerpt: EXCERPT,
    cited: true,
    ...overrides,
  };
  return {
    id: "graphlink-knowledge-citations-v1",
    name: KNOWLEDGE_CITATION_RECORD,
    argumentsJson: "{}",
    result: JSON.stringify({ version: 1, sources: [source], unverifiedIds: [] }),
    isError: false,
  };
}

describe("verified knowledge citation chips", () => {
  it("opens the exact source excerpt and original URL at its section", () => {
    render(<ChatCitationChips content={"Stop writers [" + ID + "]"} toolInvocations={[record()]} />);
    const chip = screen.getByRole("button", { name: /Open Database recovery at Restore and verify/ });
    expect(chip).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "Source section: Restore and verify" }))
      .toHaveTextContent(EXCERPT);
    expect(screen.getByText(/Offset 18–80/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open original at section" }))
      .toHaveAttribute("href", "https://example.org/ops/recovery.md#restore-and-verify");
    fireEvent.click(chip);
    expect(screen.queryByRole("region", { name: /Source section/ })).not.toBeInTheDocument();
  });

  it("marks invented and not-sent IDs unverified, never creating a real source link", () => {
    render(<ChatCitationChips content={"[" + NOT_SENT + "] [" + ID + "] [" + NOT_SENT + "]"}
      toolInvocations={[record({ cited: false })]} />);
    expect(screen.getAllByText(/Unverified \[kb:/)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Open Database recovery/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(citationsForReply("[" + ID + "]", [record({ cited: false })])[0].source).toBeNull();
  });

  it("refuses model-forged metadata without a trusted ledger", () => {
    const fabricated: ToolInvocationData = {
      ...record(), name: "other.tool",
    };
    expect(citationsForReply("[" + ID + "]", [fabricated])[0].source).toBeNull();
    expect(citationsForReply("[" + ID + "]", [{ ...record(), result: "not JSON" }])[0].source).toBeNull();
    expect(citationsForReply("No citations", [record()])).toEqual([]);
  });

  it("opens local-only sections in place without unsafe hrefs", () => {
    const local = record({
      sourceFile: "/home/user/documents/recovery.md",
      sourceUri: "/home/user/documents/recovery.md",
    });
    render(<ChatCitationChips content={"[" + ID + "]"} toolInvocations={[local]} />);
    fireEvent.click(screen.getByRole("button", { name: /Open Database recovery/ }));
    expect(screen.getByRole("region", { name: /Source section/ })).toHaveTextContent(EXCERPT);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("allows only http(s) URLs and never turns a hostile URI into a navigation", () => {
    const baseline = citationsForReply("[" + ID + "]", [record()])[0].source;
    expect(baseline).not.toBeNull();
    if (!baseline) throw new Error("expected verified citation");
    expect(sectionUrl({ ...baseline, sourceUri: "javascript:alert(1)" })).toBeNull();
    expect(sectionUrl({ ...baseline, sourceUri: "file:///etc/passwd" })).toBeNull();
    expect(sectionUrl({ ...baseline, section: "Unsectioned" }))
      .toBe("https://example.org/ops/recovery.md");
  });
});
