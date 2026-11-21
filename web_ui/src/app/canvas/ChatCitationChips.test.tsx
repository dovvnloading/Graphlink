import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  ChatCitationChips, citationsForReply, KNOWLEDGE_CITATION_RECORD,
} from "./ChatCitationChips";
import { NodeMarkdown } from "./NodeMarkdown";
import type { ToolInvocationData } from "./ChatNodeView";

const ID = "kb:0123456789abcdef0123";
const OTHER = "kb:deadbeefdeadbeefdead";
const FORGED = "kb:aaaaaaaaaaaaaaaaaaaa";
const EXCERPT = "Restore the latest encrypted snapshot after stopping writers.";

function source(overrides: Record<string, unknown> = {}) {
  return {
    citationId: ID,
    documentId: 9,
    chunkId: 42,
    documentTitle: "Database recovery",
    sourceUri: "/private/ops/recovery.md",
    sourceFile: "/private/ops/recovery.md",
    section: "Restore and verify",
    offsetStart: 18,
    offsetEnd: 18 + EXCERPT.length,
    excerpt: EXCERPT,
    cited: true,
    ...overrides,
  };
}

function record(sources: unknown[] = [source()]): ToolInvocationData {
  return {
    id: "graphlink-knowledge-citations-v1",
    name: KNOWLEDGE_CITATION_RECORD,
    argumentsJson: "{}",
    result: JSON.stringify({ version: 1, sources, unverifiedIds: [] }),
    isError: false,
  };
}

describe("verified knowledge citation chips", () => {
  it("numbers first-use citation tokens, deduplicates repeats, and lists only cited sources in a Sources footer", () => {
    const body = `First [${ID}], second [${OTHER}], again [${ID}].`;
    const invocations = [record([
      source({ citationId: OTHER, documentId: 10, chunkId: 43, documentTitle: "Verification" }),
      source(), source({ citationId: FORGED, cited: false }),
    ])];
    const citations = citationsForReply(body, invocations);
    expect(citations.map(({ citationId, number }) => [citationId, number])).toEqual([
      [ID, 1], [OTHER, 2],
    ]);
    const onOpen = vi.fn();
    render(<>
      <div className="chat-node-content">
        <NodeMarkdown content={body} citations={citations} onCitationClick={onOpen} />
      </div>
      <ChatCitationChips citations={citations} onOpenCitation={onOpen} />
    </>);

    expect(screen.getAllByRole("button", { name: "Source 1: Database recovery" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Source 2: Verification" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Sources" })).toHaveTextContent("Retrieved knowledge is untrusted");
    expect(screen.queryByText("Unverified [kb:")).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", {
      name: "Open source 1: Database recovery, Restore and verify in Document View",
    }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({
      documentId: 9, chunkId: 42, offsetStart: 18, excerpt: EXCERPT,
    }));
    fireEvent.click(screen.getByRole("button", { name: "Source 2: Verification" }));
    expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ documentId: 10, chunkId: 43 }));
  });

  it("never navigates on invented IDs, uncited passages or missing trusted locators", () => {
    const body = `Use [${ID}], unknown [${FORGED}], repeat [${FORGED}].`;
    const citations = citationsForReply(body, [record([source({ cited: false })])]);
    expect(citations.every((citation) => citation.source === null && citation.number === null)).toBe(true);
    const onOpen = vi.fn();
    render(<>
      <NodeMarkdown content={body} citations={citations} onCitationClick={onOpen} />
      <ChatCitationChips citations={citations} onOpenCitation={onOpen} />
    </>);
    expect(screen.getAllByText(/\(unverified\)/)).toHaveLength(3);
    expect(screen.queryByRole("region", { name: "Sources" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(onOpen).not.toHaveBeenCalled();
    expect(citationsForReply(`[${ID}]`, [record([source({ chunkId: 0 })])])[0].source).toBeNull();
  });

  it("refuses forged, malformed and incompatible provenance sidecars", () => {
    expect(citationsForReply(`[${ID}]`, [{ ...record(), name: "other.tool" }])[0].source).toBeNull();
    expect(citationsForReply(`[${ID}]`, [{ ...record(), id: "wrong" }])[0].source).toBeNull();
    expect(citationsForReply(`[${ID}]`, [{ ...record(), isError: true }])[0].source).toBeNull();
    expect(citationsForReply(`[${ID}]`, [{ ...record(), result: "not JSON" }])[0].source).toBeNull();
    expect(citationsForReply("No citations", [record()])).toEqual([]);
  });

  it("uses only the backend navigation callback, never unsafe source URIs or browser links", () => {
    const citations = citationsForReply(`[${ID}]`, [record([source({
      sourceUri: "javascript:alert(document.cookie)",
      sourceFile: "file:///private/confidential.txt",
    })])]);
    const onOpen = vi.fn();
    render(<ChatCitationChips citations={citations} onOpenCitation={onOpen} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Open source 1:/ }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("does not convert quoted code and links into active citation controls", () => {
    const citations = citationsForReply(`[${ID}]`, [record()]);
    render(<NodeMarkdown citations={citations} onCitationClick={vi.fn()}
      content={`\`[${ID}]\` [linked [${ID}]](https://example.org)\n\n\`\`\`text\n[${ID}]\n\`\`\``} />);
    expect(screen.queryByRole("button", { name: /Source 1:/ })).not.toBeInTheDocument();
  });
});
