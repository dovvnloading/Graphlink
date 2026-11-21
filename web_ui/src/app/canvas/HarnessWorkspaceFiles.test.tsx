import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HarnessWorkspaceFiles } from "./AgentWorkspaceBrowser";
import type { HarnessWorkspaceListing } from "./harnessWorkspaceFiles";

function listing(path: string): HarnessWorkspaceListing {
  return {
    root: "/work/project", isScratch: false, path, truncated: false,
    entries: path === "" ? [
      { name: "docs", path: "docs", isDir: true, size: null },
      { name: "main.py", path: "main.py", isDir: false, size: 30 },
    ] : [
      { name: "guide.md", path: "docs/guide.md", isDir: false, size: 12 },
    ],
  };
}

describe("HarnessWorkspaceFiles", () => {
  it("browses nested folders, opens files, navigates up and refreshes", async () => {
    const user = userEvent.setup();
    const onList = vi.fn(async (path: string) => listing(path));
    const onOpen = vi.fn(async (_path: string) => {});
    const onReveal = vi.fn();
    render(<HarnessWorkspaceFiles onList={onList} onOpen={onOpen} onReveal={onReveal} />);

    expect(onList).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    expect(await screen.findByRole("button", { name: "Open folder docs" })).toBeInTheDocument();
    expect(onList).toHaveBeenCalledWith("");
    expect(screen.getByText("Bound folder: /work/project")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open folder docs" }));
    const guide = await screen.findByRole("button", { name: "Open guide.md in Document View" });
    expect(onList).toHaveBeenCalledWith("docs");
    await user.click(guide);
    expect(onOpen).toHaveBeenCalledWith("docs/guide.md", "/work/project");
    await user.click(screen.getByRole("button", { name: "Refresh workspace files" }));
    await waitFor(() => expect(onList).toHaveBeenCalledTimes(3));
    await user.click(screen.getByRole("button", { name: "Up one folder" }));
    expect(await screen.findByRole("button", { name: "Open main.py in Document View" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Hide files" }));
    expect(screen.queryByRole("region", { name: "Workspace files" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reveal in file manager" }));
    expect(onReveal).toHaveBeenCalledOnce();
  });

  it("shows list errors and lets the user retry", async () => {
    const user = userEvent.setup();
    const onList = vi.fn()
      .mockRejectedValueOnce(new Error("Folder is no longer accessible"))
      .mockResolvedValue(listing(""));
    render(<HarnessWorkspaceFiles onList={onList} onOpen={vi.fn()} onReveal={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Folder is no longer accessible");
    await user.click(screen.getByRole("button", { name: "Refresh workspace files" }));
    expect(await screen.findByRole("button", { name: "Open folder docs" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports unsupported file previews without closing the browser", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn().mockRejectedValue(new Error("Binary files cannot be opened"));
    render(<HarnessWorkspaceFiles onList={async (path) => listing(path)} onOpen={onOpen} onReveal={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    await user.click(await screen.findByRole("button", { name: "Open main.py in Document View" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Binary files cannot be opened");
    expect(screen.getByRole("region", { name: "Workspace files" })).toBeInTheDocument();
  });

  it("navigates to later pages of a flat folder and back", async () => {
    const user = userEvent.setup();
    const onList = vi.fn(async (path: string, cursor?: string): Promise<HarnessWorkspaceListing> => ({
      ...listing(path),
      entries: cursor ? [
        { name: "file_300.txt", path: "file_300.txt", isDir: false, size: 1 },
      ] : [
        { name: "file_000.txt", path: "file_000.txt", isDir: false, size: 1 },
      ],
      truncated: !cursor,
      nextCursor: cursor ? null : "page-one-end",
    }));
    const onOpen = vi.fn(async (_path: string) => {});
    render(<HarnessWorkspaceFiles onList={onList} onOpen={onOpen} onReveal={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    expect(await screen.findByText(/Use Next page to see more files/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    const later = await screen.findByRole("button", { name: "Open file_300.txt in Document View" });
    expect(onList).toHaveBeenCalledWith("", "page-one-end");
    await user.click(later);
    expect(onOpen).toHaveBeenCalledWith("file_300.txt", "/work/project");
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByRole("button", { name: "Open file_000.txt in Document View" }))
      .toBeInTheDocument();
  });

  it("refreshes the root and removes stale entries when a trusted folder is revoked", async () => {
    const user = userEvent.setup();
    let trustChanged!: () => void;
    const onTrustChanged = vi.fn((listener: () => void) => {
      trustChanged = listener;
      return () => {};
    });
    const scratch: HarnessWorkspaceListing = {
      root: "/scratch/agent", isScratch: true, path: "", truncated: false,
      entries: [{ name: "notes.md", path: "notes.md", isDir: false, size: 15 }],
    };
    const onList = vi.fn().mockResolvedValueOnce(listing("")).mockResolvedValue(scratch);
    const onOpen = vi.fn(async (_path: string, _root: string) => {});
    render(<HarnessWorkspaceFiles onList={onList} onOpen={onOpen} onReveal={vi.fn()}
      onTrustChanged={onTrustChanged} />);

    await user.click(screen.getByRole("button", { name: "Browse files" }));
    expect(await screen.findByText("Bound folder: /work/project")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open main.py in Document View" })).toBeInTheDocument();

    act(() => trustChanged());
    expect(screen.queryByText("Bound folder: /work/project")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open main.py in Document View" })).not.toBeInTheDocument();
    expect(await screen.findByText("Scratch: /scratch/agent")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open notes.md in Document View" }));
    expect(onOpen).toHaveBeenCalledWith("notes.md", "/scratch/agent");
  });

  it("ignores an in-flight directory listing from before a trust change", async () => {
    const user = userEvent.setup();
    let trustChanged!: () => void;
    let finishOld!: (value: HarnessWorkspaceListing) => void;
    const onList = vi.fn()
      .mockImplementationOnce(() => new Promise<HarnessWorkspaceListing>((resolve) => { finishOld = resolve; }))
      .mockResolvedValue({ ...listing(""), root: "/scratch/agent", isScratch: true, entries: [] });
    render(<HarnessWorkspaceFiles onList={onList} onOpen={vi.fn()} onReveal={vi.fn()}
      onTrustChanged={(listener) => { trustChanged = listener; return () => {}; }} />);

    await user.click(screen.getByRole("button", { name: "Browse files" }));
    act(() => trustChanged());
    expect(await screen.findByText("Scratch: /scratch/agent")).toBeInTheDocument();
    await act(async () => finishOld(listing("")));
    expect(screen.getByText("Scratch: /scratch/agent")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open main.py in Document View" })).not.toBeInTheDocument();
  });

  it("adds agent workspace files using the listed root and shows import progress", async () => {
    const user = userEvent.setup();
    const onAddToKnowledge = vi.fn().mockResolvedValue({ jobId: "file-job" });
    let emit!: (payload: Record<string, unknown>) => void;
    const onSubscribeKnowledgeIngest = (listener: (payload: Record<string, unknown>) => void) => {
      emit = listener;
      return () => {};
    };
    render(<HarnessWorkspaceFiles onList={async (path) => listing(path)}
      onOpen={vi.fn()} onReveal={vi.fn()} onAddToKnowledge={onAddToKnowledge}
      onSubscribeKnowledgeIngest={onSubscribeKnowledgeIngest} />);
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    expect(screen.queryByRole("button", { name: "Add docs to knowledge" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Add main.py to knowledge" }));
    expect(onAddToKnowledge).toHaveBeenCalledWith("main.py", "/work/project");

    act(() => emit({ jobs: [{
      jobId: "file-job", status: "running", filesTotal: 1, filesProcessed: 0,
      added: 0, updated: 0, duplicates: 0, unchanged: 0, skipped: 0,
      failedFiles: 0, error: "", warnings: [],
    }] }));
    expect(await screen.findByLabelText("Knowledge import progress")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add main.py to knowledge" })).toBeDisabled();

    act(() => emit({ jobs: [{
      jobId: "file-job", status: "completed", filesTotal: 1, filesProcessed: 1,
      added: 0, updated: 0, duplicates: 1, unchanged: 0, skipped: 0,
      failedFiles: 0, error: "", warnings: [],
    }] }));
    expect(await screen.findByText("main.py is already in this workspace's knowledge.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add main.py to knowledge" })).toBeEnabled();
  });

  it("reports a file import failure without misreporting a successful ingestion", async () => {
    const user = userEvent.setup();
    const onAddToKnowledge = vi.fn().mockResolvedValue({ jobId: "file-job" });
    let emit!: (payload: Record<string, unknown>) => void;
    render(<HarnessWorkspaceFiles onList={async (path) => listing(path)}
      onOpen={vi.fn()} onReveal={vi.fn()} onAddToKnowledge={onAddToKnowledge}
      onSubscribeKnowledgeIngest={(listener) => { emit = listener; return () => {}; }} />);
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    await user.click(await screen.findByRole("button", { name: "Add main.py to knowledge" }));
    act(() => emit({ jobs: [{
      jobId: "file-job", status: "completed", filesTotal: 1, filesProcessed: 1,
      added: 0, updated: 0, duplicates: 0, unchanged: 0, skipped: 0,
      failedFiles: 1, error: "", warnings: ["main.py: File changed during import"],
    }] }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not add main.py to knowledge: main.py: File changed during import",
    );
  });

  it("shows an empty-folder state", async () => {
    const user = userEvent.setup();
    const onList = vi.fn(async () => ({ ...listing(""), entries: [], truncated: false }));
    render(<HarnessWorkspaceFiles onList={onList} onOpen={vi.fn()} onReveal={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    expect(await screen.findByText("No files in this folder.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
  });
});
