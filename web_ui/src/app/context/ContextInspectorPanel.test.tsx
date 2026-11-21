import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContextInspectorPanel, type ContextPreview } from "./ContextInspectorPanel";

const id = "kb:0123456789abcdef0123";
const preview: ContextPreview = {
  nodeId: "n4", model: "local-model", provider: "ollama", leavesDevice: false,
  sources: [
    { sourceId: "knowledge:" + id, origin: "ops/recovery.md",
      kind: "knowledge_hit", trust: "retrieved", tokenCount: 35,
      sentTokenCount: 35, included: true, truncated: false,
      leavesDevice: false, reason: "within budget", citationId: id,
      documentTitle: "Recovery", sourceFile: "ops/recovery.md",
      section: "Restoring", },
  ],
  lastReplyCitations: {
    replyNodeId: "n3",
    sources: [
      { citationId: id, documentTitle: "Recovery",
        sourceFile: "ops/recovery.md", section: "Restoring", cited: true },
      { citationId: "kb:bbbbbbbbbbbbbbbbbbbb", documentTitle: "Uncited",
        sourceFile: "ops/uncited.md", section: "Other", cited: false },
    ],
    unverifiedIds: ["kb:aaaaaaaaaaaaaaaaaaaa"],
  },
};

describe("Context Inspector citation provenance", () => {
  it("shows which passages entered the draft context and which supported the previous reply", () => {
    render(<ContextInspectorPanel preview={preview} loading={false} error={null}
      onRefresh={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(id + " · Recovery · Restoring")).toBeInTheDocument();
    const included = screen.getByRole("region", { name: "Included knowledge hits" });
    expect(included).toHaveTextContent("Recovery · Restoring");
    expect(included).toHaveTextContent(id + " · ops/recovery.md");
    const audit = screen.getByRole("region", { name: "Next chat context" });
    expect(audit).toHaveTextContent("Cited · Recovery · Restoring");
    expect(audit).toHaveTextContent("Not cited · Uncited · Other");
    expect(audit).toHaveTextContent("Unverified · kb:aaaaaaaaaaaaaaaaaaaa");
  });

  it("keeps the old panel intact for chats without knowledge", () => {
    render(<ContextInspectorPanel preview={{ ...preview, sources: [], lastReplyCitations: undefined }}
      loading={false} error={null} onRefresh={vi.fn()} onClose={vi.fn()} />);
    expect(screen.queryByText("Latest reply · cited knowledge passages")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Included knowledge hits" })).toHaveTextContent(
      "No knowledge passages are included.",
    );
  });
});
