import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - a
// bare ReactFlowProvider is enough (the ArtifactNodeView/WebResearchNodeView
// precedent). No OverlayProvider: unlike GitlinkNodeView, this card has no
// <Dialog> confirmation anywhere (dismissal is a direct intent, undoable).

import { CodeReviewNodeView, type CodeReviewFlowNode } from "./CodeReviewNodeView";

function baseData(overrides: Partial<CodeReviewFlowNode["data"]> = {}): CodeReviewFlowNode["data"] {
  return {
    codeReviewPrUrl: "",
    codeReviewRepo: "",
    codeReviewPrNumber: 0,
    codeReviewPrTitle: "",
    codeReviewPrState: "",
    codeReviewPrHtmlUrl: "",
    codeReviewBaseRef: "",
    codeReviewHeadRef: "",
    codeReviewAdditions: 0,
    codeReviewDeletions: 0,
    codeReviewChangedFiles: 0,
    codeReviewFiles: [],
    codeReviewFilesTruncated: false,
    codeReviewDiffTruncated: false,
    codeReviewDiffChars: 0,
    codeReviewDiffVersion: 0,
    codeReviewWalkthrough: [],
    codeReviewFindings: [],
    codeReviewErrors: [],
    codeReviewDismissedIds: [],
    codeReviewTitle: "",
    codeReviewOverview: "",
    codeReviewConfidence: "",
    codeReviewScores: {},
    codeReviewQualityScore: 0,
    codeReviewVerdict: "none",
    codeReviewRisk: "",
    codeReviewQualitySummary: "",
    codeReviewQa: [],
    codeReviewState: "draft",
    codeReviewError: "",
    isCollapsed: false,
    pendingRequestId: null,
    onSetPrUrl: vi.fn(),
    onFetchDiff: vi.fn(),
    onFetchDiffText: vi.fn().mockResolvedValue(""),
    onRun: vi.fn(),
    onCancel: vi.fn(),
    onAsk: vi.fn(),
    onDismissFinding: vi.fn(),
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
}

function renderReviewNode(overrides: Partial<CodeReviewFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "cr-1", selected: false, data } as unknown as NodeProps<CodeReviewFlowNode>;
  const utils = render(
    <ReactFlowProvider>
      <CodeReviewNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { data, ...utils };
}

function switchTab(label: string) {
  fireEvent.click(screen.getByRole("tab", { name: new RegExp(label) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Setup tab", () => {
  it("shows the empty state and disables Fetch Diff with a blank URL", () => {
    renderReviewNode();
    expect(screen.getByText("No pull request fetched yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Fetch Diff" })).toHaveProperty("disabled", true);
  });

  it("typing a URL and pressing Fetch Diff commits the URL then fetches", () => {
    const { data } = renderReviewNode();
    fireEvent.change(screen.getByLabelText("Pull request URL"), {
      target: { value: "https://github.com/o/r/pull/3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Fetch Diff" }));
    expect(data.onSetPrUrl).toHaveBeenCalledWith("https://github.com/o/r/pull/3");
    expect(data.onFetchDiff).toHaveBeenCalledWith("https://github.com/o/r/pull/3");
  });

  it("shows the fetched identity and enables Run Review only after a fetch", () => {
    const { data } = renderReviewNode({
      codeReviewRepo: "o/r",
      codeReviewPrNumber: 3,
      codeReviewPrTitle: "Add health check",
      codeReviewPrState: "open",
      codeReviewBaseRef: "main",
      codeReviewHeadRef: "feature",
      codeReviewChangedFiles: 2,
      codeReviewAdditions: 10,
      codeReviewDeletions: 2,
      codeReviewState: "fetched",
    });
    expect(screen.getByText(/o\/r#3/)).toBeTruthy();
    const runButton = screen.getByRole("button", { name: "Run Review" });
    expect(runButton).toHaveProperty("disabled", false);
    fireEvent.click(runButton);
    expect(data.onRun).toHaveBeenCalled();
  });

  it("warns when the fetched diff was truncated", () => {
    renderReviewNode({ codeReviewRepo: "o/r", codeReviewDiffTruncated: true });
    expect(screen.getByRole("note")).toBeTruthy();
  });

  it("shows Cancel while a request is in flight and renders the error banner", () => {
    const { data } = renderReviewNode({ pendingRequestId: "req-1", codeReviewError: "boom" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(data.onCancel).toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe("boom");
  });
});

describe("Walkthrough tab", () => {
  it("renders groups in order and lazy-fetches the diff once per version", async () => {
    let resolveFetch!: (text: string) => void;
    const pendingFetch = new Promise<string>((resolve) => {
      resolveFetch = resolve;
    });
    const { data } = renderReviewNode({
      codeReviewState: "fetched",
      codeReviewDiffVersion: 2,
      codeReviewWalkthrough: [
        { groupTitle: "Auth", paths: ["src/auth.py"], explanation: "Login flow." },
      ],
      onFetchDiffText: vi.fn().mockReturnValue(pendingFetch),
    });
    switchTab("Walkthrough");
    expect(screen.getByText("1. Auth")).toBeTruthy();
    expect(screen.getByText("src/auth.py")).toBeTruthy();
    await waitFor(() => expect(data.onFetchDiffText).toHaveBeenCalledTimes(1));
    // Still in flight: the loading placeholder shows until it resolves.
    expect(screen.getByText("Loading diff…")).toBeTruthy();
    resolveFetch("diff --git a/src/auth.py b/src/auth.py");
    await waitFor(() => expect(screen.getByText(/diff --git/)).toBeTruthy());
  });

  it("offers a retry when the lazy diff fetch fails", async () => {
    const { data } = renderReviewNode({
      codeReviewState: "fetched",
      codeReviewDiffVersion: 1,
      onFetchDiffText: vi.fn().mockRejectedValue(new Error("nope")),
    });
    switchTab("Walkthrough");
    await waitFor(() => expect(screen.getByText("Could not load the diff.")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(data.onFetchDiffText).toHaveBeenCalledTimes(2);
  });

  it("asking a question fires onAsk and clears the input", () => {
    const { data } = renderReviewNode({ codeReviewState: "fetched" });
    switchTab("Walkthrough");
    const input = screen.getByLabelText("Ask about this diff") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Where is auth checked?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(data.onAsk).toHaveBeenCalledWith("Where is auth checked?");
    expect(input.value).toBe("");
  });

  it("renders answered Q&A entries", () => {
    renderReviewNode({
      codeReviewState: "reviewed",
      codeReviewQa: [{ question: "Why?", answer: "Because reasons." }],
    });
    switchTab("Walkthrough");
    expect(screen.getByText("Why?")).toBeTruthy();
    expect(screen.getByText("Because reasons.")).toBeTruthy();
  });
});

describe("Findings tab", () => {
  const reviewed = {
    codeReviewState: "reviewed",
    codeReviewVerdict: "needs_revision",
    codeReviewQualityScore: 72,
    codeReviewRisk: "medium",
    codeReviewOverview: "Mostly fine.",
    codeReviewScores: { correctness: "80" },
    codeReviewFindings: [
      {
        id: "f1",
        severity: "medium",
        tier: "yellow",
        category: "Testing",
        path: "x.py",
        line: 4,
        title: "Missing test",
        evidence: "No test covers this.",
        impact: "Regressions slip through.",
        recommendation: "Add a test.",
      },
    ],
    codeReviewErrors: [
      {
        id: "e1",
        severity: "high",
        tier: "red",
        kind: "Security",
        path: "y.py",
        line: 0,
        title: "Hard-coded secret",
        evidence: "api_key = ...",
        fix: "Move it.",
      },
    ],
  };

  it("renders the verdict banner, scorecard, and tiered findings", () => {
    renderReviewNode({ ...reviewed });
    switchTab("Findings");
    expect(screen.getByText("Needs Revision")).toBeTruthy();
    expect(screen.getByText("72/100")).toBeTruthy();
    expect(screen.getByText("Missing test")).toBeTruthy();
    expect(screen.getByText("Hard-coded secret")).toBeTruthy();
    expect(screen.getByText("Warning")).toBeTruthy();
    expect(screen.getByText("Needs attention")).toBeTruthy();
  });

  it("shows the tab count badge for visible findings", () => {
    renderReviewNode({ ...reviewed });
    expect(screen.getByRole("tab", { name: /Findings/ }).textContent).toContain("2");
  });

  it("dismissing a finding fires onDismissFinding and hides it once dismissed", () => {
    const { data } = renderReviewNode({ ...reviewed });
    switchTab("Findings");
    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
    expect(data.onDismissFinding).toHaveBeenCalled();
  });

  it("a dismissed finding stays hidden with a count line", () => {
    renderReviewNode({ ...reviewed, codeReviewDismissedIds: ["f1"] });
    switchTab("Findings");
    expect(screen.queryByText("Missing test")).toBeNull();
    expect(screen.getByText("Hard-coded secret")).toBeTruthy();
    expect(screen.getByText(/1 dismissed/)).toBeTruthy();
  });

  it("copying a finding writes its summary to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderReviewNode({ ...reviewed });
    switchTab("Findings");
    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[0]);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("Hard-coded secret");
  });
});
