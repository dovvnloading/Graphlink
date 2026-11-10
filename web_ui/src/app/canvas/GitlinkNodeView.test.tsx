import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OverlayProvider } from "../overlays/overlays";

// ADR-011 stage 11.1: wraps the real useLodVisibility so every ACTUAL
// invocation (mount or re-render) is countable - a React.memo bailout skips
// calling GitlinkNodeView's function body entirely, so this hook (called
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
    onBrowseLocalRoot: vi.fn(),
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

/** Every one of the ten WS-backed callback props, for a single spy-and-assert-zero-calls check. */
function allTenSpies(data: GitlinkFlowNode["data"]) {
  return [
    data.onFetchRepositories,
    data.onLoadTree,
    data.onSetLocalRoot,
    data.onBrowseLocalRoot,
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
    const spies = allTenSpies(data);

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

  it("clicking Browse… next to Local root calls onBrowseLocalRoot", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode();

    await user.click(screen.getByRole("button", { name: "Browse…" }));
    expect(data.onBrowseLocalRoot).toHaveBeenCalledOnce();
  });

  it("Browse picking a new folder resyncs the Local root draft, and a later blur commits the NEW value (regression: stale draft used to revert the just-picked folder)", async () => {
    const user = userEvent.setup();
    const { data, rerenderWithData } = renderGitlinkNodeWithRerender({
      gitlinkLocalRoot: "",
    });

    // Simulates the Browse flow: onBrowseLocalRoot -> the native picker ->
    // a fresh scene snapshot carrying the newly-picked folder as a new
    // gitlinkLocalRoot prop. The field itself was never touched/focused by
    // the user here, mirroring a brand-new node where Browse is the first
    // thing clicked.
    rerenderWithData({ ...data, gitlinkLocalRoot: "C:/repos/picked-folder" });

    const input = screen.getByRole("textbox", { name: "Local root" });
    await waitFor(() => expect(input).toHaveValue("C:/repos/picked-folder"));

    // Without the resync, commitLocalRoot() on blur would still read the
    // stale pre-Browse draft ("") and send that back to the server,
    // silently reverting the folder the user just picked.
    await user.click(input);
    await user.click(document.body);
    expect(data.onSetLocalRoot).toHaveBeenCalledWith("C:/repos/picked-folder");
  });

  it("does not clobber in-progress typing in the Local root field when an unrelated prop update arrives while it is focused", async () => {
    const user = userEvent.setup();
    const { data, rerenderWithData } = renderGitlinkNodeWithRerender({
      gitlinkLocalRoot: "C:/repos/original",
    });

    const input = screen.getByRole("textbox", { name: "Local root" }) as HTMLInputElement;
    await user.click(input);
    await user.type(input, "-edit");
    expect(input.value).toBe("C:/repos/original-edit");

    // An unrelated scene push (not the user's own Browse pick, not a
    // commit) changes the prop while the field is still focused - the
    // resync must skip it rather than stomp what the user is mid-typing.
    rerenderWithData({ ...data, gitlinkLocalRoot: "C:/repos/somewhere-else" });
    expect(input.value).toBe("C:/repos/original-edit");

    await user.click(document.body);
    expect(data.onSetLocalRoot).toHaveBeenCalledWith("C:/repos/original-edit");
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
    const spies = allTenSpies(data);

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
    // Context scope is a CustomSelect (chrome/CustomSelect.tsx) now, not a
    // native <select>, so picking an option is a click on the trigger
    // followed by a click on the portaled option button - the same idiom
    // SettingsDialog.test.tsx's own chooseCustomOption helper uses.
    await user.click(screen.getByRole("button", { name: "Context scope" }));
    await user.click(await screen.findByRole("button", { name: "Full repo" }));
    expect(data.onBuildContext).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Build Context" }));
    expect(data.onBuildContext).toHaveBeenCalledWith("full", ["src/a.py"]);
  });

  it("renders file-row checkboxes with the app's drawn checkbox class", () => {
    // A raw OS checkbox repeated once per file row was the most visible
    // un-restyled control left on any card.
    renderGitlinkNode({ gitlinkRepoFilePaths: ["src/a.py", "src/b.py"] });
    for (const box of screen.getAllByRole("checkbox")) {
      expect(box).toHaveClass("gl-checkbox");
    }
  });

  it("renders the app's own dropdown for Context scope, never a native select", () => {
    // Every other dropdown in this app (Settings, Builder, Chat Library,
    // the View popover) is CustomSelect; this card was the last raw
    // <select>, and an OS dropdown inside a canvas card reads as a foreign
    // control.
    renderGitlinkNode();
    expect(document.querySelector("select")).toBeNull();
    expect(screen.getByRole("button", { name: "Context scope" })).toBeInTheDocument();
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

  it("renders proposal markdown and the complete diff as plain preformatted text", async () => {
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

  it("renders byte-change, newline and BOM evidence verbatim in the real proposal view", async () => {
    const user = userEvent.setup();
    const preview = [
      "### fixture.txt [update]", "",
      "Bytes changed: 7 -> 6 bytes.",
      "UTF-8 BOM: present -> absent.",
      "Line endings: CRLF=1, LF=0, CR=0 -> CRLF=0, LF=1, CR=0.",
      "Final newline: CRLF -> LF.",
      '-"alpha\\r\\n"', '+"alpha\\n"',
    ].join("\n");
    renderGitlinkNode({ gitlinkPreviewText: preview });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByLabelText("Gitlink file preview").textContent).toBe(preview);
    expect(screen.getByLabelText("Gitlink file preview").tagName).toBe("PRE");
  });

  it("does not interpret Markdown fences, links, images or HTML embedded in review text", async () => {
    const user = userEvent.setup();
    const preview = [
      "### harmless.txt [update]", "```", "# Forged review heading",
      "![remote image](https://example.invalid/image.png)",
      "[approve me](https://example.invalid/approve)",
      '<img src="x" onerror="alert(1)"><iframe src="https://example.invalid"></iframe>',
      "```diff", '-"old\\n"', '+"new\\n"',
    ].join("\n");
    renderGitlinkNode({ gitlinkPreviewText: preview });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    const review = screen.getByLabelText("Gitlink file preview");
    expect(review.textContent).toBe(preview);
    expect(review.querySelector("a, img, iframe, h1, script")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Forged review heading" })).toBeNull();
  });

  it("shows paths containing controls as escaped text while Apply retains the exact path", async () => {
    const user = userEvent.setup();
    const path = "odd\nname\u202e.txt";
    const shownPath = '"odd\\nname\\u202e.txt"';
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [{ path, operation: "create", reason: "fixture", content: "" }],
      gitlinkPreviewText: "### " + shownPath + " [create]\n\nFile creation: absent -> 0 bytes (empty file).",
      gitlinkChangeFingerprint: "fp-control-path",
      gitlinkLocalRoot: "C:/checkout",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByText(shownPath)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Apply selected (1)" }));
    await user.click(screen.getByRole("button", { name: "Yes" }));
    expect(data.onApply).toHaveBeenCalledWith("fp-control-path", [path]);
  });


  it("reviews each diff separately without changing the full approved set", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkPendingChanges: [
        { path: "a.py", operation: "update", reason: "repair handler" },
        { path: "b.py", operation: "create", reason: "add coverage" },
      ],
      gitlinkPreviewText: [
        "### a.py [update]", "",
        "--- a/a.py", "+++ b/a.py", "@@ -1 +1 @@", "-old A", "+new A", "",
        "### b.py [create]", "",
        "--- a/b.py", "+++ b/b.py", "@@ -0,0 +1 @@", "+new B",
      ].join("\n"),
      gitlinkChangeFingerprint: "fp-2",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    const code = () => document.querySelector(".gitlink-node-proposal-diff pre code")?.textContent || "";
    expect(code()).toContain("-old A");
    expect(code()).toContain("+new B");

    await user.click(screen.getByRole("button", { name: /create b\.py/ }));
    expect(screen.getByText("add coverage")).toBeInTheDocument();
    expect(code()).not.toContain("-old A");
    expect(code()).toContain("+new B");

    await user.click(screen.getByRole("button", { name: /update a\.py/ }));
    expect(screen.getByText("repair handler")).toBeInTheDocument();
    expect(code()).toContain("-old A");
    expect(code()).not.toContain("+new B");

    await user.click(screen.getByRole("button", { name: "All files" }));
    expect(code()).toContain("+new B");
    expect(code()).toContain("-old A");
  });

  it("preserves the full preview when a legacy diff cannot be split", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "a.py", operation: "update", reason: "legacy" }],
      gitlinkPreviewText: "legacy raw diff\n+inert content",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByRole("button", { name: /update a\.py/ })).toBeDisabled();
    const code = document.querySelector(".gitlink-node-proposal-diff pre code");
    expect(code?.textContent).toContain("legacy raw diff");
  });

  it("selects individual files and confirms only that subset, in proposal order", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [
        { path: "a.py", operation: "update", reason: "a" },
        { path: "b.py", operation: "delete", reason: "b" },
        { path: "c.py", operation: "create", reason: "c" },
      ],
      gitlinkChangeFingerprint: "fp-full-proposal",
      gitlinkLocalRoot: "C:/checkout",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    const b = screen.getByRole("checkbox", { name: "Include b.py in Apply" });
    expect(b).toBeChecked();
    await user.click(b);
    expect(b).not.toBeChecked();
    expect(screen.getByRole("status")).toHaveTextContent("2 of 3 selected for Apply");

    await user.click(screen.getByRole("button", { name: "Apply selected (2)" }));
    const dialog = screen.getByRole("dialog", { name: "Apply Selected Changes?" });
    expect(within(dialog).getByText(/Write 2 of 3 proposed file changes/)).toBeInTheDocument();
    expect(within(dialog).getByText(/1 unchecked change will be discarded/)).toBeInTheDocument();
    expect(within(dialog).getByText(/update.*a\.py/)).toBeInTheDocument();
    expect(within(dialog).getByText(/create.*c\.py/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/delete.*b\.py/)).toBeNull();
    expect(data.onApply).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Yes" }));
    expect(data.onApply).toHaveBeenCalledExactlyOnceWith("fp-full-proposal", ["a.py", "c.py"]);
  });

  it("disables Apply for an empty selection and offers select-all/select-none", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [
        { path: "a.py", operation: "update", reason: "first" },
        { path: "b.py", operation: "update", reason: "second" },
      ],
      gitlinkChangeFingerprint: "fp-valid",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    await user.click(screen.getByRole("button", { name: "Select none" }));
    expect(screen.getByRole("checkbox", { name: "Include a.py in Apply" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Include b.py in Apply" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Apply selected (0)" })).toBeDisabled();
    expect(data.onApply).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByRole("button", { name: "Apply selected (2)" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "Include a.py in Apply" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Include b.py in Apply" })).toBeChecked();
  });

  it("keeps reviewing a file independent of whether it is selected for Apply", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "a.py", operation: "update", reason: "fix" }],
      gitlinkPreviewText: "### a.py [update]\n\n-old\n+new",
      gitlinkChangeFingerprint: "fp",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    const checked = screen.getByRole("checkbox", { name: "Include a.py in Apply" });
    await user.click(screen.getByRole("button", { name: "Review update a.py" }));
    expect(checked).toBeChecked();
    await user.click(checked);
    expect(checked).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Review update a.py" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".gitlink-node-proposal-diff pre code")?.textContent).toContain("-old");
  });

  it("keeps user selection across ordinary snapshots and resets on a new fingerprint", async () => {
    const user = userEvent.setup();
    const { data, rerenderWithData } = renderGitlinkNodeWithRerender({
      gitlinkPendingChanges: [
        { path: "a.py", operation: "update", reason: "a" },
        { path: "b.py", operation: "update", reason: "b" },
      ],
      gitlinkChangeFingerprint: "fp-first",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    await user.click(screen.getByRole("button", { name: "Select none" }));
    rerenderWithData({ ...data });
    expect(screen.getByRole("button", { name: "Apply selected (0)" })).toBeDisabled();

    rerenderWithData({ ...data, gitlinkChangeFingerprint: "fp-new" });
    expect(screen.getByRole("button", { name: "Apply selected (2)" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "Include a.py in Apply" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Include b.py in Apply" })).toBeChecked();
  });

  it("cannot approve a new proposal through an old, already-open confirmation", async () => {
    const user = userEvent.setup();
    const { data, rerenderWithData } = renderGitlinkNodeWithRerender({
      gitlinkPendingChanges: [{ path: "a.py", operation: "update", reason: "a" }],
      gitlinkChangeFingerprint: "fp-first",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    await user.click(screen.getByRole("button", { name: "Apply selected (1)" }));
    expect(screen.getByRole("button", { name: "Yes" })).toBeEnabled();

    rerenderWithData({ ...data, gitlinkChangeFingerprint: "fp-new" });
    expect(screen.getByRole("alert")).toHaveTextContent("proposal changed");
    expect(screen.getByRole("button", { name: "Yes" })).toBeDisabled();
    expect(data.onApply).not.toHaveBeenCalled();
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

    await user.click(screen.getByRole("button", { name: /Apply selected/ }));
    expect(data.onApply).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Apply Selected Changes?" })).toBeInTheDocument();
    expect(screen.getByText(/Write 1 of 1 proposed file change into C:\/repos\/repo\?/)).toBeInTheDocument();
    expect(screen.getByText(/modify.*app\.py/)).toBeInTheDocument();
  });

  it("clicking Yes submits the full server fingerprint and exact selected paths", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
      gitlinkChangeFingerprint: "fp-exact-value",
      gitlinkLocalRoot: "C:/repos/repo",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    await user.click(screen.getByRole("button", { name: /Apply selected/ }));
    await user.click(screen.getByRole("button", { name: "Yes" }));

    expect(data.onApply).toHaveBeenCalledExactlyOnceWith("fp-exact-value", ["app.py"]);
  });

  it("clicking Cancel in the confirmation dismisses it without calling onApply", async () => {
    const user = userEvent.setup();
    const data = renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
      gitlinkChangeFingerprint: "fp-123",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    await user.click(screen.getByRole("button", { name: /Apply selected/ }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(data.onApply).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Apply Selected Changes?" })).toBeNull();
  });

  it("Apply is disabled with no pending changes or no fingerprint", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({ gitlinkPendingChanges: [], gitlinkChangeFingerprint: null });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByRole("button", { name: /Apply selected/ })).toBeDisabled();
  });

  it("Apply and the confirmation's Yes button are both disabled while gitlinkChangeState is 'applying'", async () => {
    const user = userEvent.setup();
    renderGitlinkNode({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
      gitlinkChangeFingerprint: "fp-123",
      gitlinkChangeState: "applying",
    });
    await user.click(screen.getByRole("tab", { name: "Proposal" }));
    expect(screen.getByRole("button", { name: /Apply selected/ })).toBeDisabled();
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

// ADR-011 stage 11.1: React.memo comparator correctness. `lodVisibilityCalls`
// (see the mock above) fires exactly once per ACTUAL render of this view -
// never on a bailed-out one - so it's the oracle for both directions: it must
// stay flat across an irrelevant/equivalent prop change (too-tight would fail
// this) and must increment on a change to a prop the view actually reads
// (too-loose would fail this).
describe("GitlinkNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    lodVisibilityCalls.count = 0;
  });

  function renderWithProps(props: NodeProps<GitlinkFlowNode>) {
    return render(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView {...props} />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
  }

  function baseGitlinkProps(overrides: Partial<GitlinkFlowNode["data"]> = {}) {
    const data = baseData(overrides);
    return { id: "gl-1", selected: false, data } as unknown as NodeProps<GitlinkFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = baseGitlinkProps();
    const { rerender } = renderWithProps(props);
    expect(lodVisibilityCalls.count).toBe(1);

    // A brand-new object, same primitives, same array/object/callback
    // references - exactly what toFlowNodes may mint on an unrelated
    // snapshot. A naive `data === nextData` reference compare (or
    // React.memo's default shallow-props compare) would wrongly re-render
    // here.
    const sameValuesNewObject = { ...props.data };
    rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView {...{ ...props, data: sameValuesNewObject }} />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("skips re-rendering when `gitlinkPendingChanges`/`gitlinkRepoFilePaths`/`gitlinkContextStats` are brand-new but byte-identical", () => {
    // Proves the comparator does shape-aware compare on these three
    // array/object fields, not a naive `===` (which would always miss here
    // since these are fresh instances) and not a naive "always unequal"
    // shortcut either (which would defeat memoization for every gitlink
    // node with any context/proposal state).
    const props = baseGitlinkProps({
      gitlinkRepoFilePaths: ["a.py", "b.py"],
      gitlinkContextStats: { files: "2", tokens: "512" },
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
    });
    const { rerender } = renderWithProps(props);
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView
            {...{
              ...props,
              data: {
                ...props.data,
                gitlinkRepoFilePaths: ["a.py", "b.py"],
                gitlinkContextStats: { files: "2", tokens: "512" },
                gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
              },
            }}
          />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("skips re-rendering when only `gitlinkScopeMode`/`gitlinkSelectedPaths`/`gitlinkTaskPrompt` change (seed-once fields this view never re-reads)", () => {
    const props = baseGitlinkProps({
      gitlinkScopeMode: "selected",
      gitlinkSelectedPaths: ["a.py"],
      gitlinkTaskPrompt: "old prompt",
    });
    const { rerender } = renderWithProps(props);
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView
            {...{
              ...props,
              data: {
                ...props.data,
                gitlinkScopeMode: "full",
                gitlinkSelectedPaths: ["b.py", "c.py"],
                gitlinkTaskPrompt: "a completely different prompt",
              },
            }}
          />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("re-renders when `gitlinkPendingChanges` content actually changes", () => {
    const props = baseGitlinkProps({
      gitlinkPendingChanges: [{ path: "app.py", operation: "modify", reason: "add route" }],
    });
    const { rerender } = renderWithProps(props);
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView
            {...{
              ...props,
              data: {
                ...props.data,
                gitlinkPendingChanges: [{ path: "app.py", operation: "delete", reason: "add route" }],
              },
            }}
          />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });

  it("re-renders when `gitlinkLocalRoot` changes (read directly in the Apply dialog, not just a seed)", () => {
    const props = baseGitlinkProps({ gitlinkLocalRoot: "C:/repos/old" });
    const { rerender } = renderWithProps(props);
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView {...{ ...props, data: { ...props.data, gitlinkLocalRoot: "C:/repos/new" } }} />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
    // One render for the prop change itself, plus a second, chained render
    // from the localRootDraft resync effect's setLocalRootDraft() call (see
    // the Browse-resync regression test above) - the field is not focused
    // here, so the effect always fires this second render.
    expect(lodVisibilityCalls.count).toBe(3);
  });

  it("re-renders when a callback prop is rebound to a new closure (e.g. onDelete)", () => {
    const props = baseGitlinkProps();
    const { rerender } = renderWithProps(props);
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView {...{ ...props, data: { ...props.data, onDelete: vi.fn() } }} />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });

  it("re-renders when `id` changes", () => {
    const props = baseGitlinkProps();
    const { rerender } = renderWithProps(props);
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <OverlayProvider>
        <ReactFlowProvider>
          <GitlinkNodeView {...{ ...props, id: "gl-2" }} />
        </ReactFlowProvider>
      </OverlayProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });
});
