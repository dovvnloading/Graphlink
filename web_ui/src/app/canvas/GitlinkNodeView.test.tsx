import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { OverlayProvider } from "../overlays/overlays";
import { GitlinkNodeView, type GitlinkFlowNode } from "./GitlinkNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ArtifactNodeView.test.tsx/WebResearchNodeView.test.tsx for why a bare
// ReactFlowProvider is enough here too. Also wrapped in OverlayProvider,
// unlike those two siblings - GitlinkNodeView's Apply confirmation is a real
// <Dialog> from the R2.1 overlay system (../overlays/overlays), which throws
// without a provider ancestor (see useOverlays()).

function baseData(overrides: Partial<GitlinkFlowNode["data"]> = {}): GitlinkFlowNode["data"] {
  return {
    gitlinkRepo: "",
    gitlinkBranch: "",
    gitlinkScopeMode: "selected",
    gitlinkLocalRoot: "",
    gitlinkRepoFilePaths: [],
    gitlinkSelectedPaths: [],
    gitlinkTaskPrompt: "",
    gitlinkContextStats: {},
    gitlinkContextSummary: "",
    gitlinkContextVersion: 0,
    gitlinkProposalMarkdown: "",
    gitlinkPendingChanges: [],
    gitlinkPreviewText: "",
    gitlinkChangeFingerprint: null,
    gitlinkChangeState: "",
    gitlinkError: "",
    isCollapsed: false,
    pendingRequestId: null,
    onFetchRepositories: vi.fn().mockResolvedValue([]),
    onLoadTree: vi.fn(),
    onSetLocalRoot: vi.fn(),
    onImportSnapshot: vi.fn(),
    onBuildContext: vi.fn(),
    onFetchContext: vi.fn().mockResolvedValue(""),
    onRun: vi.fn(),
    onCancel: vi.fn(),
    onApply: vi.fn(),
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
}

function renderGitlinkNode(overrides: Partial<GitlinkFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "gl-1", selected: false, data } as unknown as NodeProps<GitlinkFlowNode>;

  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <GitlinkNodeView {...props} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  return data;
}

// Directly sets the React Flow internal Zustand store's transform/zoom value
// - same technique ArtifactNodeView.test.tsx's own ZoomSetter uses.
function ZoomSetter({ zoom }: { zoom: number }) {
  const store = useStoreApi();
  useEffect(() => {
    store.setState({ transform: [0, 0, zoom] });
  }, [zoom, store]);
  return null;
}

function renderGitlinkNodeAtZoom(zoom: number, overrides: Partial<GitlinkFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "gl-1", selected: false, data } as unknown as NodeProps<GitlinkFlowNode>;

  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <ZoomSetter zoom={zoom} />
        <GitlinkNodeView {...props} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  return data;
}

// Same shell as renderGitlinkNode, but also returns RTL's `rerender` so a
// test can push a NEW `data` object at the same mounted component instance -
// needed for FIX 5 (stale-fetch race) and FIX 7 (repo/branch change resets
// selection), both of which are about how the component reacts to a prop
// change, not just its initial-mount shape.
function renderGitlinkNodeWithRerender(overrides: Partial<GitlinkFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "gl-1", selected: false, data } as unknown as NodeProps<GitlinkFlowNode>;

  const utils = render(
    <OverlayProvider>
      <ReactFlowProvider>
        <GitlinkNodeView {...props} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );

  function rerenderWithData(nextData: GitlinkFlowNode["data"]) {
    const nextProps = { id: "gl-1", selected: false, data: nextData } as unknown as NodeProps<GitlinkFlowNode>;
    utils.rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView {...nextProps} />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
  }

  return { data, rerenderWithData };
}

/** Every one of the nine WS-backed callback props, for a single spy-and-assert-zero-calls check. */
function allNineSpies(data: GitlinkFlowNode["data"]) {
  return [
    data.onFetchRepositories,
    data.onLoadTree,
    data.onSetLocalRoot,
    data.onImportSnapshot,
    data.onBuildContext,
    data.onFetchContext,
    data.onRun,
    data.onCancel,
    data.onApply,
  ] as unknown as ReturnType<typeof vi.fn>[];
}

describe("GitlinkNodeView", () => {
  // -- tabs -------------------------------------------------------------

  it("renders all three tabs and can switch between them", async () => {
    const user = userEvent.setup();
    renderGitlinkNode();

    expect(screen.getByRole("tab", { name: "Setup" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Context" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Proposal" })).toBeInTheDocument();

    // Setup is the default active tab.
    expect(screen.getByRole("button", { name: "Load Repo Tree" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Context" }));
    expect(screen.getByText("No context built yet.")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByText("No change set generated yet.")).toBeInTheDocument();
  });

  // -- collapse/expand + LOD ----------------------------------------------

  it("manual collapse hides the body and shows only the header", () => {
    renderGitlinkNode({ isCollapsed: true });
    expect(screen.getByText("Gitlink")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Setup" })).toBeNull();
  });

  it("the inline collapse chevron calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("LOD auto-collapse (zoom below threshold) also hides the body, even when isCollapsed is false", () => {
    renderGitlinkNodeAtZoom(0.2, { isCollapsed: false });
    expect(screen.getByText("Gitlink")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Setup" })).toBeNull();
  });

  it("stays expanded above the LOD threshold when isCollapsed is false", () => {
    renderGitlinkNodeAtZoom(1, { isCollapsed: false });
    expect(screen.getByRole("tab", { name: "Setup" })).toBeInTheDocument();
  });

  // -- Setup tab: repo/branch do not call any WS method until explicit Load -

  it("typing into repo/branch fields calls no WS method - only clicking Load Repo Tree does", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();
    const spies = allNineSpies(data);

    await user.type(screen.getByRole("textbox", { name: "Repository" }), "owner/repo");
    await user.type(screen.getByRole("textbox", { name: "Branch" }), "main");
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Load Repo Tree" }));
    expect(data.onLoadTree).toHaveBeenCalledWith("owner/repo", "main");
  });

  it("pressing Enter in the branch field also triggers Load Repo Tree", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();
    await user.type(screen.getByRole("textbox", { name: "Repository" }), "owner/repo");
    await user.type(screen.getByRole("textbox", { name: "Branch" }), "main{Enter}");
    expect(data.onLoadTree).toHaveBeenCalledWith("owner/repo", "main");
  });

  it("Load Repo Tree is disabled when the repo field is empty", () => {
    renderGitlinkNode();
    expect(screen.getByRole("button", { name: "Load Repo Tree" })).toBeDisabled();
  });

  it("List My Repos calls onFetchRepositories and renders the returned names as pickable options", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      onFetchRepositories: vi.fn().mockResolvedValue(["owner/repo-a", "owner/repo-b"]),
    });

    await user.click(screen.getByRole("button", { name: "List My Repos" }));
    expect(data.onFetchRepositories).toHaveBeenCalledOnce();

    await screen.findByRole("button", { name: "owner/repo-a" });
    await user.click(screen.getByRole("button", { name: "owner/repo-a" }));
    expect(screen.getByRole("textbox", { name: "Repository" })).toHaveValue("owner/repo-a");
  });

  it("local root is committed via onSetLocalRoot only on blur, not on every keystroke", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();
    const input = screen.getByRole("textbox", { name: "Local root" });

    await user.type(input, "C:/repos/thing");
    expect(data.onSetLocalRoot).not.toHaveBeenCalled();

    await user.click(document.body);
    expect(data.onSetLocalRoot).toHaveBeenCalledWith("C:/repos/thing");
  });

  it("pressing Enter in the Local root field commits via onSetLocalRoot exactly once, not twice (FIX 8)", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();
    const input = screen.getByRole("textbox", { name: "Local root" });

    await user.type(input, "C:/repos/thing{Enter}");

    // Enter used to both call commitLocalRoot() directly AND trigger .blur(),
    // which itself fires the onBlur={commitLocalRoot} handler - double
    // dispatching the WS intent for one keypress.
    expect(data.onSetLocalRoot).toHaveBeenCalledTimes(1);
    expect(data.onSetLocalRoot).toHaveBeenCalledWith("C:/repos/thing");
  });

  it("Import Repo Snapshot uses the current repo/branch draft field values", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();

    await user.type(screen.getByRole("textbox", { name: "Repository" }), "owner/repo");
    await user.type(screen.getByRole("textbox", { name: "Branch" }), "dev");
    await user.click(screen.getByRole("button", { name: "Import Repo Snapshot" }));
    expect(data.onImportSnapshot).toHaveBeenCalledWith("owner/repo", "dev");
  });

  // -- Setup tab: file tree is pure client-side state ----------------------

  it("file-tree filter/select-visible/clear-selection never call any WS method", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkRepoFilePaths: ["src/a.py", "src/b.py", "readme.md"],
    });
    const spies = allNineSpies(data);

    await user.type(screen.getByRole("textbox", { name: "Filter files" }), "src/");
    expect(screen.getByText("src/a.py")).toBeInTheDocument();
    expect(screen.queryByText("readme.md")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Select Visible" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear Selection" }));
    expect(screen.getByText("0 selected")).toBeInTheDocument();

    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it("switching to a different repo/branch resets the file-tree selection (FIX 7)", async () => {
    const user = userEvent.setup();
    const { data, rerenderWithData } = renderGitlinkNodeWithRerender({
      gitlinkRepo: "owner/repo-a",
      gitlinkBranch: "main",
      gitlinkRepoFilePaths: ["a.py", "b.py"],
    });

    await user.click(screen.getByLabelText("a.py"));
    await user.click(screen.getByLabelText("b.py"));
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    const dataForRepoB = {
      ...data,
      gitlinkRepo: "owner/repo-b",
      gitlinkBranch: "dev",
      gitlinkRepoFilePaths: ["c.py"],
    };
    rerenderWithData(dataForRepoB);

    await waitFor(() => expect(screen.getByText("0 selected")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Build Context" }));
    // The stale repo-A paths ("a.py"/"b.py") must not ride into the build for
    // repo B - selectedPaths was reset to [] by the repo/branch change.
    expect(dataForRepoB.onBuildContext).toHaveBeenCalledWith("selected", []);
  });

  it("does NOT reset the file-tree selection on the initial mount (only on a later repo/branch change)", () => {
    renderGitlinkNode({
      gitlinkRepo: "owner/repo-a",
      gitlinkBranch: "main",
      gitlinkRepoFilePaths: ["a.py", "b.py"],
      gitlinkSelectedPaths: ["a.py"],
    });
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });

  it("Build Context sends the current scope mode and selected paths only when clicked", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({ gitlinkRepoFilePaths: ["src/a.py", "src/b.py"] });

    await user.click(screen.getByLabelText("src/a.py"));
    await user.selectOptions(screen.getByLabelText("Context scope"), "full");
    expect(data.onBuildContext).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Build Context" }));
    expect(data.onBuildContext).toHaveBeenCalledWith("full", ["src/a.py"]);
  });

  it("Generate Change Set passes the trimmed task-prompt draft directly to onRun", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();

    await user.type(screen.getByRole("textbox", { name: "Task prompt" }), "  add a health check  ");
    await user.click(screen.getByRole("button", { name: "Generate Change Set" }));
    expect(data.onRun).toHaveBeenCalledWith("add a health check");
  });

  it("the Cancel button appears only while pendingRequestId is set and calls onCancel", async () => {
    const user = userEvent.setup();
    expect(renderGitlinkNode({ pendingRequestId: null })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();

    const data = renderGitlinkNode({ pendingRequestId: "req-1" });
    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    await user.click(cancelBtn);
    expect(data.onCancel).toHaveBeenCalledOnce();
  });

  // -- Context tab: fetch-once-per-summary ---------------------------------

  it("onFetchContext is called exactly once the first time the Context tab is opened, and not again on a second open with the same summary", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkContextSummary: "2 files, 512 tokens",
      onFetchContext: vi.fn().mockResolvedValue("<context/>"),
    });

    await user.click(screen.getByRole("tab", { name: "Context" }));
    await waitFor(() => expect(data.onFetchContext).toHaveBeenCalledTimes(1));
    await screen.findByText("<context/>");

    await user.click(screen.getByRole("tab", { name: "Setup" }));
    await user.click(screen.getByRole("tab", { name: "Context" }));
    expect(data.onFetchContext).toHaveBeenCalledTimes(1);
  });

  it("does not fetch context at all while the summary is empty", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({ gitlinkContextSummary: "" });
    await user.click(screen.getByRole("tab", { name: "Context" }));
    expect(data.onFetchContext).not.toHaveBeenCalled();
  });

  it("shows the context stats/summary immediately with no fetch required for those two", () => {
    renderGitlinkNode({
      gitlinkContextSummary: "2 files, 512 tokens",
      gitlinkContextStats: { files: "2", tokens: "512" },
    });
    // Stats/summary are already present in props - switching is required to
    // even see the tab, but the values themselves need no network call.
  });

  it("refetches when gitlinkContextVersion changes even if gitlinkContextSummary stays IDENTICAL (FIX 6)", async () => {
    const user = userEvent.setup();
    const onFetchContext = vi
      .fn()
      .mockResolvedValueOnce("<context>build 1</context>")
      .mockResolvedValueOnce("<context>build 2</context>");

    const { data, rerenderWithData } = renderGitlinkNodeWithRerender({
      gitlinkContextSummary: "Scanned 1 files.",
      gitlinkContextVersion: 1,
      onFetchContext,
    });

    await user.click(screen.getByRole("tab", { name: "Context" }));
    await waitFor(() => expect(onFetchContext).toHaveBeenCalledTimes(1));
    await screen.findByText("<context>build 1</context>");

    // A second, different Build Context call (e.g. a different single-file
    // selection) happens to produce the exact same human-readable summary
    // string - only the version counter tells the two builds apart. Keying
    // the re-fetch guard on the summary alone would incorrectly treat this
    // as "nothing changed" and skip the refetch.
    rerenderWithData({
      ...data,
      gitlinkContextSummary: "Scanned 1 files.",
      gitlinkContextVersion: 2,
    });

    await waitFor(() => expect(onFetchContext).toHaveBeenCalledTimes(2));
    await screen.findByText("<context>build 2</context>");
  });

  // R5.3 post-review FIX 5: an older in-flight fetch resolving AFTER a newer
  // one must never clobber the newer result.
  it("a stale (older) context fetch resolving after a newer one does not overwrite the newer content (FIX 5)", async () => {
    const user = userEvent.setup();
    let resolveFirst!: (xml: string) => void;
    let resolveSecond!: (xml: string) => void;
    const firstFetch = new Promise<string>((resolve) => {
      resolveFirst = resolve;
    });
    const secondFetch = new Promise<string>((resolve) => {
      resolveSecond = resolve;
    });
    const onFetchContext = vi.fn().mockReturnValueOnce(firstFetch).mockReturnValueOnce(secondFetch);

    const { data, rerenderWithData } = renderGitlinkNodeWithRerender({
      gitlinkContextSummary: "build A - 1 file",
      gitlinkContextVersion: 1,
      onFetchContext,
    });

    await user.click(screen.getByRole("tab", { name: "Context" }));
    await waitFor(() => expect(onFetchContext).toHaveBeenCalledTimes(1));

    // A second, newer Build Context completes while the first fetch is still
    // in flight - the version bump (mirroring a real Build Context call) is
    // enough to kick off fetch #2.
    rerenderWithData({ ...data, gitlinkContextSummary: "build B - 2 files", gitlinkContextVersion: 2 });
    await waitFor(() => expect(onFetchContext).toHaveBeenCalledTimes(2));

    // Resolve the NEWER fetch first, then the OLDER one arrives late - the
    // classic out-of-order race the guard exists for.
    resolveSecond("<context>NEW BUILD B</context>");
    await screen.findByText("<context>NEW BUILD B</context>");

    resolveFirst("<context>STALE BUILD A</context>");
    expect(screen.queryByText("<context>STALE BUILD A</context>")).toBeNull();
    expect(screen.getByText("<context>NEW BUILD B</context>")).toBeInTheDocument();
  });

  // -- Proposal tab: markdown/diff rendering + security --------------------

  it("renders the proposal markdown and the diff (as syntax-highlighted fenced code) through ReactMarkdown", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkProposalMarkdown: "# Add health check\n\nThis adds **a route**.",
      gitlinkPreviewText: "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,2 @@\n+# new line",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByRole("heading", { name: "Add health check" })).toBeInTheDocument();
    expect(screen.getByText("a route")).toBeInTheDocument();
    expect(document.querySelector("pre code")).not.toBeNull();
  });

  it("SECURITY: a proposal markdown string containing a literal <img onerror> tag never becomes a real rendered img element", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkProposalMarkdown: 'Look at this: <img src="x" onerror="alert(1)"> nothing happened.',
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("SECURITY: a diff string containing a literal <img onerror> tag never becomes a real rendered img element", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkPreviewText: '+<img src="x" onerror="alert(1)">',
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  // -- Apply confirmation ---------------------------------------------------

  it("clicking Apply does NOT call onApply directly - it opens a confirmation first", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
      gitlinkChangeFingerprint: "fp-123",
      gitlinkLocalRoot: "C:/repos/repo",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(data.onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Apply Changes?" })).toBeInTheDocument();
    expect(screen.getByText(/Write 1 file change into C:\/repos\/repo\?/)).toBeInTheDocument();
    expect(screen.getByText(/modify.*app\.py/)).toBeInTheDocument();
  });

  it("clicking Yes in the confirmation calls onApply with EXACTLY the gitlinkChangeFingerprint prop value", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
      gitlinkChangeFingerprint: "fp-exact-value",
      gitlinkLocalRoot: "C:/repos/repo",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await user.click(screen.getByRole("button", { name: "Yes" }));

    expect(data.onApply).toHaveBeenCalledExactlyOnceWith("fp-exact-value");
  });

  it("clicking Cancel in the confirmation dismisses it without calling onApply", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
      gitlinkChangeFingerprint: "fp-123",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(data.onApply).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Apply Changes?" })).toBeNull();
  });

  it("Apply is disabled with no pending changes or no fingerprint", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({ gitlinkPendingChanges: [], gitlinkChangeFingerprint: null });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("Apply and the confirmation's Yes button are both disabled while gitlinkChangeState is 'applying'", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
      gitlinkChangeFingerprint: "fp-123",
      gitlinkChangeState: "applying",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("shows the gitlinkError inline in the Proposal tab when changeState is 'previewed'", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkChangeState: "previewed",
      gitlinkError: "The context changed since this proposal was generated - please review again.",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(
      screen.getByText("The context changed since this proposal was generated - please review again."),
    ).toBeInTheDocument();
  });

  // -- card-level menu ----------------------------------------------------

  it("the node-level right-click menu shows exactly Collapse/Expand + Delete Node - no dock action", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();

    fireEvent.contextMenu(screen.getByText("Gitlink"));
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Collapse");
    expect(items[1]).toHaveTextContent("Delete Node");
    expect(screen.queryByRole("menuitem", { name: /Dock/ })).toBeNull();

    await user.click(items[0]);
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();

    fireEvent.contextMenu(screen.getByText("Gitlink"));
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(data.onDelete).toHaveBeenCalledOnce();
  });

  it("Escape and outside-click both close the node-level menu", async () => {
    const user = userEvent.setup();
    renderGitlinkNode();
    const header = screen.getByText("Gitlink");

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
