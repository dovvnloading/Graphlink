import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeSearchDialog } from "./KnowledgeSearchDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { makeRequestOnlyTransport as makeTransport } from "../../lib/ws/transport.testUtils";

function OpenKnowledgeButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("knowledge", "dialog")}>
      open knowledge
    </button>
  );
}

async function setup(onOpenDocumentView?: (content: string, label: string) => void) {
  const user = userEvent.setup();
  const fake = makeTransport();
  fake.request.mockImplementation(async (_topic, intent) => {
    if (intent === "listDocuments") return { documents: [] };
    if (intent === "listWatchedFolders") return { folders: [] };
    return {};
  });
  let importListener: ((payload: Record<string, unknown>) => void) | null = null;
  fake.transport.subscribe = (topic, listener) => {
    if (topic === "knowledge-ingest") importListener = listener;
    return () => { importListener = null; };
  };
  function emitImportJobs(jobs: Record<string, unknown>[]) {
    act(() => { importListener?.({ jobs }); });
  }
  render(
    <OverlayProvider>
      <OpenKnowledgeButton />
      <KnowledgeSearchDialog transport={fake.transport} onOpenDocumentView={onOpenDocumentView} />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole("button", { name: "open knowledge" }));
  return { user, ...fake, emitImportJobs };
}

const RESULT_A = {
  chunkId: 1,
  documentId: 10,
  documentTitle: "Fox Story",
  sourceUri: "https://example.com/fox",
  text: "The quick brown fox jumps over the lazy dog.",
  offsetStart: 0,
  offsetEnd: 45,
};

const RESULT_B = {
  chunkId: 2,
  documentId: 11,
  documentTitle: "Local Notes",
  sourceUri: "C:\\Users\\me\\notes.txt",
  text: "Some local notes content.",
  offsetStart: 100,
  offsetEnd: 126,
};

describe("KnowledgeSearchDialog", () => {

  it("opens the native multiple-file picker through the knowledge intent", async () => {
    const { user, intents, request } = await setup();
    request.mockResolvedValueOnce({ jobId: "first", status: "queued" });

    await user.click(screen.getByRole("button", { name: "Add files" }));

    expect(intents).toContainEqual(["knowledge", "addFiles", []]);
  });

  it("opens the native folder picker through the knowledge intent", async () => {
    const { user, intents, request } = await setup();
    request.mockResolvedValueOnce({ jobId: "second", status: "queued" });

    await user.click(screen.getByRole("button", { name: "Add folder" }));

    expect(intents).toContainEqual(["knowledge", "addFolder", []]);
  });

  it("renders progress events, disables concurrent imports, and cancels the correct job", async () => {
    const { user, intents, request, emitImportJobs } = await setup();
    emitImportJobs([{
      jobId: "running-job", kind: "folder", workspaceId: 12, status: "running",
      filesTotal: 4, filesProcessed: 2, bytesTotal: 200, bytesProcessed: 100,
      added: 1, updated: 1, duplicates: 0, unchanged: 0, skipped: 1,
      failedFiles: 0, lastFile: "notes.md", error: "", warnings: [],
      cancelRequested: false,
    }]);

    expect(screen.getByText(/2\/4 files/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Files processed" })).toHaveAttribute("value", "2");
    expect(screen.getByRole("button", { name: "Add files" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add folder" })).toBeDisabled();
    request.mockResolvedValueOnce({ cancelRequested: true });
    await user.click(screen.getByRole("button", { name: "Cancel import" }));
    expect(intents).toContainEqual(["knowledge", "cancelIngest", ["running-job"]]);

    emitImportJobs([{
      jobId: "running-job", kind: "folder", workspaceId: 12, status: "cancelled",
      filesTotal: 4, filesProcessed: 2, bytesTotal: 200, bytesProcessed: 100,
      added: 1, updated: 1, duplicates: 0, unchanged: 0, skipped: 1,
      failedFiles: 0, lastFile: "notes.md", error: "", warnings: [],
      cancelRequested: true,
    }]);
    expect(screen.queryByRole("button", { name: "Cancel import" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add files" })).toBeEnabled();
  });

  it("submitting a query requests knowledge/search with the typed text and a default k", async () => {
    const { user, intents, request } = await setup();
    request.mockResolvedValueOnce({ results: [] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "brown fox");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(intents).toContainEqual(["knowledge", "search", ["brown fox", 10]]);
  });

  it('renders "N sources used" with N matching the real result count', async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [RESULT_A, RESULT_B] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("2 sources used")).toBeInTheDocument();
  });

  it("singular phrasing for exactly one result", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [RESULT_A] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("1 source used")).toBeInTheDocument();
  });

  it("shows an explicit empty state instead of a false zero-sources count", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "nothing here");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("No sources found")).toBeInTheDocument();
  });

  it("shows each hit's keyword and semantic matches, including their retrieval ranks", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [
      { ...RESULT_A, rankSignals: { fts5: 2, vector: 1 } },
      { ...RESULT_B, rankSignals: { fts5: 1 } },
      { ...RESULT_B, chunkId: 3, documentTitle: "Semantic Only", rankSignals: { vector: 3 } },
    ] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "wetland bird");
    await user.click(screen.getByRole("button", { name: "Search" }));

    const groups = await screen.findAllByRole("group", { name: "Match signals" });
    expect(groups).toHaveLength(3);
    expect(within(groups[0]).getByText("Keyword #2")).toBeInTheDocument();
    expect(within(groups[0]).getByText("Semantic #1")).toBeInTheDocument();
    expect(within(groups[1]).getByText("Keyword #1")).toBeInTheDocument();
    expect(within(groups[1]).queryByText(/Semantic/)).not.toBeInTheDocument();
    expect(within(groups[2]).getByText("Semantic #3")).toBeInTheDocument();
    expect(within(groups[2]).queryByText(/Keyword/)).not.toBeInTheDocument();
  });

  it("does not invent match signals for older search responses", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [RESULT_A] });
    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText("Fox Story");
    expect(screen.queryByRole("group", { name: "Match signals" })).not.toBeInTheDocument();
  });

  it("expanding a result reveals the exact cited excerpt and its offset range", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [RESULT_A] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText("Fox Story");

    await user.click(screen.getByText("Fox Story"));

    expect(screen.getByText(RESULT_A.text)).toBeInTheDocument();
    expect(screen.getByText("Offset 0–45")).toBeInTheDocument();
  });

  it("a real http(s) source gets a working Open source link", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [RESULT_A] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByText("Fox Story"));

    const link = screen.getByRole("link", { name: "Open source" });
    expect(link).toHaveAttribute("href", "https://example.com/fox");
  });

  it("a local file path source gets no Open source link (no OS jump mechanism exists)", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [RESULT_B] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByText("Local Notes"));

    expect(screen.queryByRole("link", { name: "Open source" })).not.toBeInTheDocument();
  });

  it("shows dropped-file names rather than private Windows or POSIX upload paths", async () => {
    const windowsPath = String.raw`C:\Users\Ada\.graphlink\knowledge\uploads\id\0\lighthouse.md`;
    const posixPath = "/home/ada/.graphlink/knowledge/uploads/id/1/notes.md";
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [
      { ...RESULT_B, sourceUri: windowsPath, sourceFile: windowsPath, documentTitle: "Lighthouse" },
      { ...RESULT_B, chunkId: 3, documentId: 13, sourceUri: posixPath,
        sourceFile: posixPath, documentTitle: "Notes" },
    ] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("2 sources used")).toBeInTheDocument();
    expect(screen.getByText("lighthouse.md")).toBeInTheDocument();
    expect(screen.getByText("notes.md")).toBeInTheDocument();
    expect(screen.queryByText(windowsPath)).not.toBeInTheDocument();
    expect(screen.queryByText(posixPath)).not.toBeInTheDocument();
  });

  it("a failed request shows an error instead of a silent empty state", async () => {
    const { user, request } = await setup();
    request.mockRejectedValueOnce(new Error("boom"));

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/search failed/i);
  });

  it("the Search button is disabled for a blank query", async () => {
    await setup();
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  });

  it("pressing Enter in the input submits the search", async () => {
    const { user, intents, request } = await setup();
    request.mockResolvedValueOnce({ results: [] });

    await user.type(screen.getByPlaceholderText(/search your ingested knowledge base/i), "enter query{Enter}");

    expect(intents).toContainEqual(["knowledge", "search", ["enter query", 10]]);
  });

  const LIBRARY_DOCUMENT = {
    documentId: 12, title: "guide.md", type: "text/markdown",
    sourceUri: "/workspace/guide.md", sizeBytes: 2048,
    chunks: 4, addedAt: "2026-11-19T14:00:00Z",
    status: "indexed", canReindex: true,
  };

  it("lists workspace documents with metadata, size, chunks, date and status", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) =>
      intent === "listDocuments" ? { documents: [LIBRARY_DOCUMENT] } : {},
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("guide.md")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Title" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Type" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Size" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Chunks" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Added" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();
    expect(screen.getByText("2.0 KiB")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("Indexed")).toBeInTheDocument();
    expect(screen.getByText("MD")).toBeInTheDocument();
  });

  it("labels indexed chats and branches readably with their stored sizes", async () => {
    const { user, request } = await setup();
    const chat = { ...LIBRARY_DOCUMENT, documentId: 27, title: "Saved chat",
      type: "text/graph", sourceUri: "graph:27", sizeBytes: 1536, canReindex: false };
    const branch = { ...LIBRARY_DOCUMENT, documentId: 28, title: "Indexed branch",
      type: "text/branch", sourceUri: "branch:abc", sizeBytes: 128, canReindex: false };
    request.mockImplementation(async (_topic, intent) =>
      intent === "listDocuments" ? { documents: [chat, branch] } : {},
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    const chatRow = (await screen.findByText("Saved chat")).closest("tr");
    const branchRow = screen.getByText("Indexed branch").closest("tr");
    expect(chatRow).not.toBeNull();
    expect(branchRow).not.toBeNull();
    expect(within(chatRow as HTMLElement).getByText("Chat")).toBeInTheDocument();
    expect(within(chatRow as HTMLElement).getByText("1.5 KiB")).toBeInTheDocument();
    expect(within(branchRow as HTMLElement).getByText("Chat branch")).toBeInTheDocument();
    expect(within(branchRow as HTMLElement).getByText("128 B")).toBeInTheDocument();
    expect(screen.queryByText("text/graph")).not.toBeInTheDocument();
  });

  it("does not leak a local path through the Library document title tooltip", async () => {
    const privatePath = String.raw`C:\Users\Ada\.graphlink\knowledge\uploads\id\0\guide.md`;
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) =>
      intent === "listDocuments" ? {
        documents: [{ ...LIBRARY_DOCUMENT, sourceUri: privatePath }],
      } : {},
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("guide.md")).toHaveAttribute("title", "guide.md");
  });

  it("opens indexed content in the existing Document View", async () => {
    const onOpen = vi.fn();
    const { user, request, intents } = await setup(onOpen);
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [LIBRARY_DOCUMENT] };
      if (intent === "openDocument") return { title: "guide.md", content: "# Guide" };
      return {};
    });
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await user.click(await screen.findByRole("button", { name: "Open guide.md in Document View" }));

    expect(intents).toContainEqual(["knowledge", "openDocument", [12]]);
    expect(onOpen).toHaveBeenCalledWith("# Guide", "guide.md");
  });

  it("requires a second confirmation before removing a document", async () => {
    const { user, request, intents } = await setup();
    let available = true;
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: available ? [LIBRARY_DOCUMENT] : [] };
      if (intent === "removeDocument") { available = false; return { removed: true }; }
      return {};
    });
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await user.click(await screen.findByRole("button", { name: "Remove guide.md" }));
    expect(intents).not.toContainEqual(["knowledge", "removeDocument", [12]]);
    await user.click(screen.getByRole("button", { name: "Confirm remove guide.md" }));
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "removeDocument", [12]]));
    expect(await screen.findByText(/No documents yet/)).toBeInTheDocument();
  });

  it("re-indexes an indexed local source without sending client filesystem paths", async () => {
    const { user, request, intents } = await setup();
    request.mockImplementation(async (_topic, intent) =>
      intent === "listDocuments" ? { documents: [LIBRARY_DOCUMENT] } : { jobId: "job-42" },
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await user.click(await screen.findByRole("button", { name: "Re-index guide.md" }));
    expect(intents).toContainEqual(["knowledge", "reindexDocument", [12]]);
  });

  it("accepts dropped browser files via bounded upload intents", async () => {
    const { request, intents } = await setup();
    const bytes = new TextEncoder().encode("dragged markdown");
    const file = new File([bytes], "dragged.md", { type: "text/markdown" });
    Object.defineProperty(file, "slice", {
      value: () => ({ arrayBuffer: async () => bytes.buffer }),
    });
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [] };
      if (intent === "beginUpload") return { uploadId: "transfer-1" };
      if (intent === "finishUpload") return { jobId: "job-1" };
      return { bytesReceived: bytes.byteLength };
    });
    fireEvent.drop(screen.getByRole("region", { name: "Drop files to index" }), {
      dataTransfer: { files: [file] },
    });
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "finishUpload", ["transfer-1"]]));
    expect(intents).toContainEqual([
      "knowledge", "beginUpload", [[{ name: "dragged.md", size: bytes.byteLength }]],
    ]);
    expect(intents.some(([topic, intent]) => topic === "knowledge" && intent === "appendUpload")).toBe(true);
  });

  it("disables re-indexing documents without a local source", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) =>
      intent === "listDocuments" ? {
        documents: [{ ...LIBRARY_DOCUMENT, canReindex: false, sourceUri: "https://example.org/page" }],
      } : {},
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("button", { name: "Re-index guide.md" })).toBeDisabled();
  });

  it("the search input has an accessible label and the embedding controls share the dialog", async () => {
    await setup();
    expect(screen.getByLabelText("Search the knowledge base")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Knowledge embeddings" })).toBeInTheDocument();
  });

  const WATCHED_FOLDER = {
    id: 42, path: "/workspace/notes", fileCount: 7,
    lastSyncAt: "2026-11-20T14:45:00Z",
    lastError: "2 items skipped during watched folder scan.",
    syncStatus: "completed",
    lastJob: {
      filesTotal: 8, filesProcessed: 8, added: 2, updated: 1,
      removed: 0, skipped: 2, failedFiles: 1, error: "",
    },
  };

  it("shows a watched folder's persisted file count, last sync time, job counts and errors", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [] };
      if (intent === "listWatchedFolders") return { folders: [WATCHED_FOLDER] };
      return {};
    });

    await user.click(screen.getByRole("button", { name: "Refresh watched folders" }));

    expect(await screen.findByText(WATCHED_FOLDER.path)).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Last successful sync" })).toBeInTheDocument();
    expect(screen.getByText("7 indexed")).toBeInTheDocument();
    expect(screen.getByText(new Date(WATCHED_FOLDER.lastSyncAt).toLocaleString())).toBeInTheDocument();
    expect(screen.getByText(/8\/8 scanned.*2 added.*1 updated.*1 failed/)).toBeInTheDocument();
    expect(screen.getByText(/Last error: 2 items skipped/)).toBeInTheDocument();
  });

  it("adds a watched folder using only the native picker intent and supports Enter", async () => {
    const { user, request, intents } = await setup();
    let folders: object[] = [];
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [] };
      if (intent === "listWatchedFolders") return { folders };
      if (intent === "addWatchedFolder") {
        folders = [{ ...WATCHED_FOLDER, fileCount: 0, lastSyncAt: null, lastError: "",
          syncStatus: "queued", lastJob: null }];
        return { folder: folders[0], job: { jobId: "sync-1", status: "queued" } };
      }
      return {};
    });

    const add = screen.getByRole("button", { name: "Add watched folder" });
    add.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(intents).toContainEqual(["knowledge", "addWatchedFolder", []]));
    expect(await screen.findByText(WATCHED_FOLDER.path)).toBeInTheDocument();
    expect(screen.getByText("0 indexed")).toBeInTheDocument();
    expect(screen.getByText("Never")).toBeInTheDocument();
    expect(intents).not.toContainEqual(["knowledge", "addWatchedFolder", [WATCHED_FOLDER.path]]);
  });

  it("does not add a watch when the native folder picker is cancelled", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [] };
      if (intent === "listWatchedFolders") return { folders: [] };
      if (intent === "addWatchedFolder") return { cancelled: true };
      return {};
    });
    await user.click(screen.getByRole("button", { name: "Add watched folder" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Add watched folder" })).toBeEnabled());
    expect(screen.getByText(/No watched folders yet/)).toBeInTheDocument();
  });

  it("syncs one folder with its id, or all folders with no arguments, using keyboard actions", async () => {
    const { user, request, intents } = await setup();
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [] };
      if (intent === "listWatchedFolders") return { folders: [WATCHED_FOLDER] };
      if (intent === "syncWatchedFolders") return { jobs: [{ folderId: 42, status: "queued" }] };
      return {};
    });
    await user.click(screen.getByRole("button", { name: "Refresh watched folders" }));

    const syncOne = await screen.findByRole("button", { name: `Sync ${WATCHED_FOLDER.path} now` });
    syncOne.focus();
    await user.keyboard(" ");
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "syncWatchedFolders", [42]]));

    await waitFor(() => expect(screen.getByRole("button", { name: "Sync all now" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Sync all now" }));
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "syncWatchedFolders", []]));
  });

  it("confirms removal, allows keeping a watch, and never deletes indexed documents", async () => {
    const { user, request, intents } = await setup();
    let folders = [WATCHED_FOLDER];
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [LIBRARY_DOCUMENT] };
      if (intent === "listWatchedFolders") return { folders };
      if (intent === "removeWatchedFolder") { folders = []; return { removed: true }; }
      return {};
    });
    await user.click(screen.getByRole("button", { name: "Refresh watched folders" }));
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    const remove = await screen.findByRole("button", { name: `Remove watched folder ${WATCHED_FOLDER.path}` });
    remove.focus();
    await user.keyboard(" ");
    expect(intents).not.toContainEqual(["knowledge", "removeWatchedFolder", [42]]);

    const keep = screen.getByRole("button", { name: `Keep watching ${WATCHED_FOLDER.path}` });
    keep.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: `Remove watched folder ${WATCHED_FOLDER.path}` })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: `Remove watched folder ${WATCHED_FOLDER.path}` }));
    const confirm = screen.getByRole("button", {
      name: `Confirm remove watched folder ${WATCHED_FOLDER.path}`,
    });
    confirm.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(intents).toContainEqual(["knowledge", "removeWatchedFolder", [42]]));
    expect(await screen.findByText(/No watched folders yet/)).toBeInTheDocument();
    expect(screen.getByText("guide.md")).toBeInTheDocument();
  });

  it("surfaces watched-folder request failures without hiding the configured folder", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: [] };
      if (intent === "listWatchedFolders") return { folders: [WATCHED_FOLDER] };
      if (intent === "syncWatchedFolders") throw new Error("Sync denied.");
      return {};
    });
    await user.click(screen.getByRole("button", { name: "Refresh watched folders" }));
    await user.click(await screen.findByRole("button", { name: `Sync ${WATCHED_FOLDER.path} now` }));
    expect(await screen.findByText("Sync denied.")).toHaveAttribute("role", "alert");
    expect(screen.getByText(WATCHED_FOLDER.path)).toBeInTheDocument();
  });

  // Adversarial-review regression: the input (unlike the Search button) was
  // never disabled while a search was pending, and the Enter-key handler
  // called runSearch() with no `searching` check - so a second Enter press
  // while the first request was still in flight fired a genuine second
  // request. Whichever response happened to resolve last silently won,
  // regardless of which was actually sent last. The fix adds the same
  // `searching` early-return to runSearch() that the Search button's
  // `disabled` already implied, so a second Enter press during an in-flight
  // search is a no-op instead of a duplicate request.
  it("a second Enter press while a search is already in flight does not fire a duplicate request", async () => {
    const { user, request } = await setup();
    let resolveFirst: (value: unknown) => void = () => {};
    request.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve; }),
    );

    const input = screen.getByPlaceholderText(/search your ingested knowledge base/i);
    await user.type(input, "first query{Enter}");
    expect(request).toHaveBeenCalledTimes(4);

    await user.clear(input);
    await user.type(input, "second query{Enter}");
    expect(request).toHaveBeenCalledTimes(4);

    resolveFirst({ results: [RESULT_A] });
    expect(await screen.findByText("Fox Story")).toBeInTheDocument();
  });

  it("limits a 5,000-document library to 50 interactive rows per page, with keyboard-accessible navigation", async () => {
    const { user, request } = await setup();
    const largeLibrary = Array.from({ length: 5_000 }, (_, index) => ({
      ...LIBRARY_DOCUMENT, documentId: index + 1, title: `doc-${index + 1}.md`,
    }));
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listDocuments") return { documents: largeLibrary };
      if (intent === "listWatchedFolders") return { folders: [] };
      return {};
    });
    await user.click(screen.getByRole("button", { name: "Refresh" }));

    const library = screen.getByRole("region", { name: "Workspace documents" });
    expect(await within(library).findByText("doc-1.md")).toBeInTheDocument();
    expect(within(library).getAllByRole("row")).toHaveLength(51); // header + 50 documents
    expect(within(library).queryByText("doc-51.md")).not.toBeInTheDocument();
    expect(within(library).getByRole("status")).toHaveTextContent("Showing 1–50 of 5000");

    const next = within(library).getByRole("button", { name: "Next documents" });
    next.focus();
    await user.keyboard("{Enter}");
    expect(within(library).getAllByRole("row")).toHaveLength(51);
    expect(within(library).getByText("doc-51.md")).toBeInTheDocument();
    expect(within(library).queryByText("doc-1.md")).not.toBeInTheDocument();
    expect(within(library).getByRole("status")).toHaveTextContent("Page 2 of 100");

    const previous = within(library).getByRole("button", { name: "Previous documents" });
    previous.focus();
    await user.keyboard(" ");
    expect(within(library).getByText("doc-1.md")).toBeInTheDocument();
    expect(previous).toBeDisabled();
  });

  it("moves focus into removal confirmation and restores it if the user keeps the document", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) =>
      intent === "listDocuments" ? { documents: [LIBRARY_DOCUMENT] } : {},
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    const remove = await screen.findByRole("button", { name: "Remove guide.md" });
    remove.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Confirm remove guide.md" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Keep guide.md" }));
    expect(screen.getByRole("button", { name: "Remove guide.md" })).toHaveFocus();
  });

  it("opens a verified search citation in Document View with the original passage offsets", async () => {
    const onOpen = vi.fn();
    const { user, request } = await setup(onOpen);
    const citationId = "kb:0123456789abcdef0123";
    request.mockResolvedValueOnce({ results: [{
      ...RESULT_A, citationId, section: "Runbook", sourceFile: "/indexed/fox.md",
    }] });
    await user.type(screen.getByLabelText("Search the knowledge base"), "fox{Enter}");
    await user.click(await screen.findByText("Fox Story"));
    const open = screen.getByRole("button", { name: "Open in Document View" });
    open.focus();
    await user.keyboard("{Enter}");

    expect(onOpen).toHaveBeenCalledWith("", "Fox Story", {
      citationId, documentId: RESULT_A.documentId, chunkId: RESULT_A.chunkId,
      documentTitle: RESULT_A.documentTitle, sourceUri: RESULT_A.sourceUri,
      sourceFile: "/indexed/fox.md", section: "Runbook",
      offsetStart: RESULT_A.offsetStart, offsetEnd: RESULT_A.offsetEnd,
      excerpt: RESULT_A.text, cited: true,
    });
    expect(screen.queryByRole("dialog", { name: "Knowledge Library" })).not.toBeInTheDocument();
  });

  it("never creates a Document View navigation action for an unverified citation", async () => {
    const { user, request } = await setup(vi.fn());
    request.mockResolvedValueOnce({ results: [{ ...RESULT_A, citationId: "made-up" }] });
    await user.type(screen.getByLabelText("Search the knowledge base"), "fox{Enter}");
    await user.click(await screen.findByText("Fox Story"));
    expect(screen.queryByRole("button", { name: "Open in Document View" })).not.toBeInTheDocument();
  });

});
