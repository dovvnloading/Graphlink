import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  WebResearchNodeView,
  type WebResearchFlowNode,
  type WebResearchResultRow,
} from "./WebResearchNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx / ConversationNodeView.test.tsx for why a bare
// ReactFlowProvider is enough here too.

function baseData(overrides: Partial<WebResearchFlowNode["data"]> = {}): WebResearchFlowNode["data"] {
  return {
    query: "",
    isCollapsed: false,
    pendingRequestId: null,
    researchStage: "",
    researchCompleted: 0,
    researchTotal: 0,
    researchActiveSourceId: null,
    researchError: "",
    researchResult: null,
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    onRun: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
}

function renderWebResearchNode(overrides: Partial<WebResearchFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "n0", selected: false, data } as unknown as NodeProps<WebResearchFlowNode>;

  render(
    <ReactFlowProvider>
      <WebResearchNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

// Directly sets the React Flow internal Zustand store's transform/zoom value
// - same technique ConversationNodeView.test.tsx's own ZoomSetter uses (a
// mounted panZoom instance doesn't exist in this direct-render test setup).
function ZoomSetter({ zoom }: { zoom: number }) {
  const store = useStoreApi();
  useEffect(() => {
    store.setState({ transform: [0, 0, zoom] });
  }, [zoom, store]);
  return null;
}

function renderWebResearchNodeAtZoom(zoom: number, overrides: Partial<WebResearchFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "n0", selected: false, data } as unknown as NodeProps<WebResearchFlowNode>;

  render(
    <ReactFlowProvider>
      <ZoomSetter zoom={zoom} />
      <WebResearchNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

function makeResult(overrides: Partial<WebResearchResultRow> = {}): WebResearchResultRow {
  return {
    requestId: "req-1",
    originalQuery: "who won the 2019 world series",
    effectiveQuery: "2019 world series winner",
    answerMarkdown: "The Nationals won.",
    sources: [],
    citations: [],
    warnings: [],
    providerSnapshot: {},
    ...overrides,
  };
}

describe("WebResearchNodeView", () => {
  it("renders the query in the input and the header label", () => {
    renderWebResearchNode({ query: "who won the 2019 world series" });
    expect(screen.getByText("Web Research")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Research query" })).toHaveValue(
      "who won the 2019 world series",
    );
  });

  it("manual collapse hides the body and shows only the header", () => {
    renderWebResearchNode({ isCollapsed: true, query: "hello" });
    expect(screen.getByText("Web Research")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("the inline collapse chevron calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const data = renderWebResearchNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("LOD auto-collapse (zoom below threshold) also hides the body, even when isCollapsed is false", () => {
    renderWebResearchNodeAtZoom(0.2, { isCollapsed: false, query: "hello" });
    expect(screen.getByText("Web Research")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("stays expanded above the LOD threshold when isCollapsed is false", () => {
    renderWebResearchNodeAtZoom(1, { isCollapsed: false, query: "hello" });
    expect(screen.getByRole("textbox", { name: "Research query" })).toBeInTheDocument();
  });

  // -- Run/Cancel ------------------------------------------------------------

  it("the Run button is disabled when the query is empty or whitespace-only", async () => {
    const user = userEvent.setup();
    renderWebResearchNode({ query: "" });
    const input = screen.getByRole("textbox", { name: "Research query" });
    const runButton = screen.getByRole("button", { name: "Run" });

    expect(runButton).toBeDisabled();
    await user.type(input, "   ");
    expect(runButton).toBeDisabled();
    await user.type(input, "real query");
    expect(runButton).toBeEnabled();
  });

  it("clicking Run calls onRun with the trimmed draft text", async () => {
    const user = userEvent.setup();
    const data = renderWebResearchNode({ query: "" });
    const input = screen.getByRole("textbox", { name: "Research query" });

    await user.type(input, "  who won the 2019 world series  ");
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(data.onRun).toHaveBeenCalledWith("who won the 2019 world series");
  });

  it("the Run button is disabled while pendingRequestId is set, even with non-empty query", async () => {
    const user = userEvent.setup();
    renderWebResearchNode({ query: "", pendingRequestId: "req-1" });
    const input = screen.getByRole("textbox", { name: "Research query" });
    await user.type(input, "real query");
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("the Cancel button is absent when pendingRequestId is null", () => {
    renderWebResearchNode({ pendingRequestId: null });
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("the Cancel button is present and calls onCancel when pendingRequestId is set", async () => {
    const user = userEvent.setup();
    const data = renderWebResearchNode({ pendingRequestId: "req-42" });
    const cancelButton = screen.getByRole("button", { name: "Cancel" });
    expect(cancelButton).toBeInTheDocument();
    await user.click(cancelButton);
    expect(data.onCancel).toHaveBeenCalledOnce();
  });

  // -- stage stepper ----------------------------------------------------------

  it("highlights the correct step as active, marks earlier steps done, and leaves later ones pending", () => {
    renderWebResearchNode({ researchStage: "extracting" });
    expect(screen.getByText("Preparing")).toHaveClass("web-research-node-step", "done");
    expect(screen.getByText("Searching")).toHaveClass("web-research-node-step", "done");
    expect(screen.getByText("Fetching")).toHaveClass("web-research-node-step", "done");
    expect(screen.getByText("Extracting")).toHaveClass("web-research-node-step", "active");
    expect(screen.getByText("Validating")).toHaveClass("web-research-node-step", "pending");
    expect(screen.getByText("Synthesizing")).toHaveClass("web-research-node-step", "pending");
  });

  it("shows no stepper before any run has started (empty researchStage)", () => {
    renderWebResearchNode({ researchStage: "" });
    expect(screen.queryByText("Preparing")).toBeNull();
  });

  it("shows the fetching-source progress line built from researchCompleted/researchTotal, never a per-source highlight", () => {
    renderWebResearchNode({
      researchStage: "fetching",
      researchCompleted: 2,
      researchTotal: 5,
      researchActiveSourceId: "opaque-source-id",
    });
    expect(screen.getByText(/Fetching source 3 of 5/)).toBeInTheDocument();
    // The opaque active-source id itself must never appear as rendered text.
    expect(screen.queryByText("opaque-source-id")).toBeNull();
  });

  it("shows no progress line when researchTotal is 0", () => {
    renderWebResearchNode({ researchStage: "searching", researchCompleted: 0, researchTotal: 0 });
    expect(screen.queryByText(/Fetching source/)).toBeNull();
  });

  it("completed collapses the stepper in favor of the answer", () => {
    renderWebResearchNode({
      researchStage: "completed",
      researchResult: makeResult({ answerMarkdown: "Final answer text." }),
    });
    expect(screen.queryByText("Preparing")).toBeNull();
    expect(screen.getByText("Final answer text.")).toBeInTheDocument();
  });

  it("shows a red failed banner with the researchError text instead of continuing the stepper", () => {
    renderWebResearchNode({ researchStage: "failed", researchError: "Search provider unavailable." });
    expect(screen.queryByText("Preparing")).toBeNull();
    const banner = screen.getByText("Search provider unavailable.");
    expect(banner).toHaveClass("web-research-node-banner", "web-research-node-banner-failed");
  });

  it("shows a gray cancelled banner, defaulting the text when researchError is empty", () => {
    renderWebResearchNode({ researchStage: "cancelled", researchError: "" });
    expect(screen.queryByText("Preparing")).toBeNull();
    const banner = screen.getByText("Research was cancelled.");
    expect(banner).toHaveClass("web-research-node-banner", "web-research-node-banner-cancelled");
  });

  // -- result: answer/warnings/sources -----------------------------------------

  it("renders the answer markdown, warnings, and per-source status chips from a completed result", () => {
    renderWebResearchNode({
      researchStage: "completed",
      researchResult: makeResult({
        answerMarkdown: "The **Nationals** won.",
        warnings: ["One source was truncated."],
        sources: [
          {
            sourceId: "src-1",
            title: "2019 World Series - Wikipedia",
            url: "https://example.com/ws",
            canonicalUrl: "https://example.com/ws",
            snippet: "",
            rank: 1,
            provider: "search",
            finalUrl: "https://example.com/ws",
            status: "accepted",
            errorCode: "",
            errorMessage: "",
            truncated: false,
            contentHash: "abc",
            citationCount: 1,
          },
          {
            sourceId: "src-2",
            title: "",
            url: "https://example.com/rejected",
            canonicalUrl: "",
            snippet: "",
            rank: 2,
            provider: "search",
            finalUrl: "",
            status: "rejected",
            errorCode: "",
            errorMessage: "",
            truncated: false,
            contentHash: "",
            citationCount: 0,
          },
        ],
      }),
    });

    expect(screen.getByText("Nationals")).toBeInTheDocument(); // bold text still renders as text
    expect(screen.getByText("One source was truncated.")).toBeInTheDocument();

    expect(screen.getByText("2019 World Series - Wikipedia")).toBeInTheDocument();
    expect(screen.getByText("accepted")).toHaveClass("web-research-node-source-status-accepted");

    expect(screen.getByText("https://example.com/rejected")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toHaveClass("web-research-node-source-status-rejected");
  });

  it("renders nothing for the result section when researchResult is null", () => {
    renderWebResearchNode({ researchResult: null });
    expect(screen.queryByText(/accepted|rejected|fetching|discovered|failed/)).toBeNull();
  });

  // -- markdown link safety ------------------------------------------------

  it("opens a real http(s) link via window.open on click", async () => {
    const user = userEvent.setup();
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    renderWebResearchNode({
      researchStage: "completed",
      researchResult: makeResult({
        answerMarkdown: "[real link](https://example.com/page)",
      }),
    });

    await user.click(screen.getByText("real link"));
    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith("https://example.com/page", "_blank", "noopener,noreferrer");
    openSpy.mockRestore();
  });

  it("never calls window.open for a javascript: href", async () => {
    const user = userEvent.setup();
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    renderWebResearchNode({
      researchStage: "completed",
      researchResult: makeResult({
        answerMarkdown: "[bad link](javascript:alert(1))",
      }),
    });

    await user.click(screen.getByText("bad link"));
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it("renders a non-http(s) link with no href at all, so middle-click/context-menu has no raw href to bypass onClick with", () => {
    // Review-found regression guard: an onClick-only guard (preventDefault +
    // scheme check) leaves the raw href on the DOM node, which the browser's
    // native middle-click (auxclick) and "open link in new tab"/"copy link
    // address" context-menu actions read directly - bypassing onClick
    // entirely. The fix omits href on the anchor altogether for any
    // non-http(s) scheme, so there is nothing for those native gestures to act on.
    renderWebResearchNode({
      researchStage: "completed",
      researchResult: makeResult({
        answerMarkdown: "[bad link](javascript:alert(1))",
      }),
    });

    const badLink = screen.getByText("bad link");
    expect(badLink.tagName).not.toBe("A");
    expect(document.querySelector("a[href]")).toBeNull();
  });

  // -- card-level menu ----------------------------------------------------

  it("the node-level right-click menu shows exactly Collapse/Expand + Delete Node - no dock action", async () => {
    const user = userEvent.setup();
    const data = renderWebResearchNode();

    fireEvent.contextMenu(screen.getByText("Web Research"));
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Collapse");
    expect(items[1]).toHaveTextContent("Delete Node");
    expect(screen.queryByRole("menuitem", { name: /Dock/ })).toBeNull();

    await user.click(items[0]);
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();

    fireEvent.contextMenu(screen.getByText("Web Research"));
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(data.onDelete).toHaveBeenCalledOnce();
  });

  it("the menu's Collapse/Expand label flips when isCollapsed is true", () => {
    renderWebResearchNode({ isCollapsed: true });
    fireEvent.contextMenu(screen.getByText("Web Research"));
    expect(screen.getByRole("menuitem", { name: "Expand" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Collapse" })).toBeNull();
  });

  it("Escape and outside-click both close the node-level menu", async () => {
    const user = userEvent.setup();
    renderWebResearchNode();
    const header = screen.getByText("Web Research");

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
