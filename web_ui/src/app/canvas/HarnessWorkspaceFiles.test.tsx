import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HarnessWorkspaceFiles } from "./HarnessWorkspaceFiles";
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
    expect(onOpen).toHaveBeenCalledWith("docs/guide.md");
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

  it("shows a capped-list notice and an empty-folder state", async () => {
    const user = userEvent.setup();
    const onList = vi.fn(async () => ({
      ...listing(""), entries: [], truncated: true, isScratch: true,
    }));
    render(<HarnessWorkspaceFiles onList={onList} onOpen={vi.fn()} onReveal={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Browse files" }));
    expect(await screen.findByText("No files in this folder.")).toBeInTheDocument();
    expect(screen.getByText(/Showing the first 300 entries/)).toBeInTheDocument();
  });
});
