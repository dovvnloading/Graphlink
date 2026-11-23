import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeSearchDialog } from "./KnowledgeSearchDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { makeRequestOnlyTransport } from "../../lib/ws/transport.testUtils";

function OpenKnowledgeButton() {
  const overlays = useOverlays();
  return <button type="button" onClick={() => overlays.open("knowledge", "dialog")}>open knowledge</button>;
}

const DOC = {
  documentId: 10, title: "report.csv", type: "text/csv", sourceUri: "C:\\Users\\me\\report.csv",
  sizeBytes: 2048, chunks: 4, addedAt: "2026-01-15T08:00:00",
  status: "indexed", canReindex: true,
};
const FOLDER = {
  id: 7, path: "/home/user/Documents", fileCount: 3, lastSyncAt: "2026-01-15T08:00:00",
  lastError: "", syncStatus: "idle", lastJob: null,
};
const RESULT = {
  chunkId: 1, documentId: 10, documentTitle: "Fox story",
  sourceUri: "https://example.com/fox", text: "The quick brown fox jumps over the lazy dog.",
  offsetStart: 0, offsetEnd: 44, citationId: "kb:aaaaaaaaaaaaaaaaaaaa",
  sourceFile: "fox.md", section: "Opening", rankSignals: { fts5: 2, vector: 1 },
};

async function setup({
  documents = [], folders = [], onOpenDocumentView,
}: {
  documents?: Array<Record<string, unknown>>;
  folders?: Array<Record<string, unknown>>;
  onOpenDocumentView?: (content: string, label: string, citation?: unknown) => void;
} = {}) {
  const user = userEvent.setup();
  const fake = makeRequestOnlyTransport();
  const listeners = new Map<string, (payload: Record<string, unknown>) => void>();
  fake.transport.subscribe = (topic, listener) => {
    listeners.set(topic, listener);
    return () => { if (listeners.get(topic) === listener) listeners.delete(topic); };
  };
  fake.transport.fireIntent = (topic, intent, args = []) => {
    fake.intents.push([topic, intent, args]);
  };
  fake.request.mockImplementation(async (_topic, intent) => {
    if (intent === "listDocuments") return { documents };
    if (intent === "listWatchedFolders") return { folders };
    if (intent === "getEmbeddingJobs") return { workspaceId: 5, jobs: [] };
    if (intent === "search") return { results: [] };
    return {};
  });
  render(
    <OverlayProvider>
      <OpenKnowledgeButton />
      <KnowledgeSearchDialog transport={fake.transport} onOpenDocumentView={onOpenDocumentView} />
      <ConfirmDialog />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole("button", { name: "open knowledge" }));
  return {
    user, ...fake,
    emit: (topic: string, payload: Record<string, unknown>) => {
      act(() => { listeners.get(topic)?.(payload); });
    },
  };
}


function documentActions(title: string) {
  const item = screen.getByText(title).closest("li");
  if (!item) throw new Error("Missing document row " + title);
  return within(item);
}

async function chooseDocumentAction(user: ReturnType<typeof userEvent.setup>, title: string, action: string) {
  await user.click(documentActions(title).getByRole("button", { name: "Actions" }));
  await user.click(await screen.findByRole("menuitem", { name: action }));
}

async function searchFor(user: ReturnType<typeof userEvent.setup>, term: string) {
  await user.type(screen.getByRole("searchbox", { name: "Search the knowledge base" }), term);
}

describe("KnowledgeSearchDialog kit library", () => {
  it("opens the xl dialog with search first, and count-labelled tabs", async () => {
    const { user } = await setup({ documents: [DOC], folders: [FOLDER] });
    expect(screen.getByRole("dialog", { name: "Knowledge library" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search the knowledge base" })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Knowledge library sections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Documents (1)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Watched folders (1)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Embeddings" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Watched folders (1)" }));
    expect(screen.getByRole("button", { name: "Sync all now" })).toBeEnabled();
  });

  it("starts native file and folder imports through the existing intents", async () => {
    const { user, intents } = await setup();
    await user.click(screen.getByRole("button", { name: "Add files" }));
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "addFiles", []]));
    await user.click(screen.getByRole("button", { name: "Add folder" }));
    expect(intents).toContainEqual(["knowledge", "addFolder", []]);
  });

  it("renders an empty drop-zone with EmptyState and accepts file drag events", async () => {
    await setup();
    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
    const dropZone = screen.getByRole("region", { name: "Drop files to index" });
    fireEvent.dragOver(dropZone);
    expect(screen.getByText("Drop files to index")).toBeInTheDocument();
    fireEvent.dragLeave(dropZone, { relatedTarget: document.body });
    expect(screen.queryByText("Drop files to index")).toBeNull();
  });

  it("shows document icons, kit status and formatted bytes in each row", async () => {
    const { user } = await setup({ documents: [DOC] });
    expect(await screen.findByText("report.csv")).toBeInTheDocument();
    expect(screen.getByText(/2.0 KB/)).toBeInTheDocument();
    expect(screen.getByText("Indexed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Re-index" })).toBeInTheDocument();
  });

  it("opens a document through its action menu and closes Knowledge Library", async () => {
    const onOpenDocumentView = vi.fn();
    const { user, intents, request } = await setup({ documents: [DOC], onOpenDocumentView });
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "openDocument") return { content: "Hello document", title: "report.csv" };
      return {};
    });
    await screen.findByText("report.csv");
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Open in Document View" }));
    await waitFor(() => expect(onOpenDocumentView).toHaveBeenCalledWith("Hello document", "report.csv"));
    expect(intents).toContainEqual(["knowledge", "openDocument", [10]]);
  });

  it("re-indexes supported sources from the actions menu", async () => {
    const { user, intents } = await setup({ documents: [DOC] });
    await screen.findByText("report.csv");
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Re-index" }));
    expect(intents).toContainEqual(["knowledge", "reindexDocument", [10]]);
  });

  it("never removes a document without explicit kit confirmation", async () => {
    const { user, intents } = await setup({ documents: [DOC] });
    await screen.findByText("report.csv");
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove…" }));
    expect(await screen.findByRole("dialog", { name: "Remove document?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(intents.some((entry) => entry[1] === "removeDocument")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove…" }));
    await user.click(await screen.findByRole("button", { name: "Remove document" }));
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "removeDocument", [10]]));
  });

  it("runs live debounced search, marks literal matches and displays rank signals", async () => {
    const { user, intents, request } = await setup();
    request.mockImplementation(async (_topic, intent) =>
      intent === "search" ? { results: [RESULT] } : {});
    await user.type(screen.getByRole("searchbox", { name: "Search the knowledge base" }), "fox");
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "search", ["fox", 10]]));
    expect(await screen.findByText("1 search result")).toBeInTheDocument();
    const marker = document.querySelector("mark");
    expect(marker).toHaveTextContent("fox");
    expect(screen.getByText("Keyword #2")).toBeInTheDocument();
    expect(screen.getByText("Semantic #1")).toBeInTheDocument();
  });

  it("opens a validated citation in Document View, with a real HTTP source link", async () => {
    const onOpenDocumentView = vi.fn();
    const { user, request } = await setup({ onOpenDocumentView });
    request.mockImplementation(async (_topic, intent) =>
      intent === "search" ? { results: [RESULT] } : {});
    await user.type(screen.getByRole("searchbox", { name: "Search the knowledge base" }), "fox");
    await screen.findByText("Fox story");
    expect(screen.getByRole("link", { name: "Open source" })).toHaveAttribute("href", "https://example.com/fox");
    await user.click(screen.getByRole("button", { name: "Open in Document View" }));
    expect(onOpenDocumentView).toHaveBeenCalledWith("", "Fox story", expect.objectContaining({
      documentId: 10, chunkId: 1, offsetStart: 0, offsetEnd: 44,
    }));
  });

  it("keeps raw local file paths out of a search-result source label", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) =>
      intent === "search" ? { results: [{ ...RESULT, sourceUri: "C:\\Users\\secret\\notes.md", sourceFile: "C:\\Users\\secret\\notes.md" }] } : {});
    await user.type(screen.getByRole("searchbox", { name: "Search the knowledge base" }), "notes");
    await screen.findByText("Fox story");
    expect(screen.getByText("notes.md")).toBeInTheDocument();
    expect(screen.queryByText(/Users\\secret/)).toBeNull();
  });

  it("does not allow a stale search response to replace a newer query's results", async () => {
    let resolveOld: ((value: unknown) => void) | undefined;
    const old = new Promise<unknown>((resolve) => { resolveOld = resolve; });
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent, args) => {
      if (intent !== "search") return {};
      return args?.[0] === "alpha" ? old : { results: [{ ...RESULT, documentTitle: "New result" }] };
    });
    const field = screen.getByRole("searchbox", { name: "Search the knowledge base" });
    await user.type(field, "alpha");
    await waitFor(() => expect(request).toHaveBeenCalledWith("knowledge", "search", ["alpha", 10]));
    await user.clear(field);
    await user.type(field, "beta");
    expect(await screen.findByText("New result")).toBeInTheDocument();
    await act(async () => { resolveOld?.({ results: [{ ...RESULT, documentTitle: "Old result" }] }); await old; });
    expect(screen.queryByText("Old result")).toBeNull();
  });

  it("shows an explicit no-results status when a search returns no hits", async () => {
    const { user } = await setup();
    await user.type(screen.getByRole("searchbox", { name: "Search the knowledge base" }), "no match");
    expect(await screen.findByText("No search results")).toBeInTheDocument();
  });

  it("shows import-job progress, disables conflicting imports and routes cancellation", async () => {
    const { user, intents, emit } = await setup();
    emit("knowledge-ingest", { jobs: [{
      jobId: "running-1", kind: "folder", workspaceId: 12, status: "running",
      filesTotal: 4, filesProcessed: 2, bytesTotal: 200, bytesProcessed: 100,
      added: 1, updated: 1, duplicates: 0, unchanged: 0, skipped: 0,
      failedFiles: 0, lastFile: "notes.md", error: "", warnings: [], cancelRequested: false,
    }] });
    expect(screen.getByRole("progressbar", { name: "Files processed" })).toHaveAttribute("aria-valuenow", "2");
    expect(screen.getByRole("button", { name: "Add files" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel import" }));
    expect(intents).toContainEqual(["knowledge", "cancelIngest", ["running-1"]]);
  });

  it("adds and syncs watched folders and confirms before removing a watch", async () => {
    const { user, intents } = await setup({ folders: [FOLDER] });
    await user.click(screen.getByRole("tab", { name: "Watched folders (1)" }));
    await user.click(screen.getByRole("button", { name: "Add watched folder" }));
    expect(intents).toContainEqual(["knowledge", "addWatchedFolder", []]);
    await user.click(screen.getByRole("button", { name: "Sync all now" }));
    expect(intents).toContainEqual(["knowledge", "syncWatchedFolders", []]);
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove watched folder…" }));
    expect(await screen.findByRole("dialog", { name: "Stop watching folder?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove watched folder" }));
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "removeWatchedFolder", [7]]));
  });

  it("navigates to the Knowledge settings section for embedding setup", async () => {
    const { user, emit, intents } = await setup();
    await user.click(screen.getByRole("tab", { name: "Embeddings" }));
    emit("app-settings", { knowledgeEmbeddingSource: "off", knowledgeEmbeddingModel: "" });
    await user.click(screen.getByRole("button", { name: "Set up embeddings" }));
    expect(intents).toContainEqual(["app-settings", "setActiveSection", ["knowledge"]]);
  });

  it("paginates large document libraries with kit buttons", async () => {
    const documents = Array.from({ length: 51 }, (_, index) => ({
      ...DOC, documentId: index + 1, title: "Document " + index + ".md",
    }));
    const { user } = await setup({ documents });
    await screen.findByRole("button", { name: "Next documents" });
    expect(screen.getByText("Document 0.md")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next documents" }));
    expect(screen.getByText("Document 50.md")).toBeInTheDocument();
  });

  it("refreshes the document list on knowledge-library topic notifications", async () => {
    const { emit, intents } = await setup();
    emit("knowledge-library", { workspaceId: 5 });
    await waitFor(() => {
      expect(intents.filter((row) => row[1] === "listDocuments").length).toBeGreaterThanOrEqual(2);
    });
  });

  it("native Add files uses the knowledge picker intent with no browser paths", async () => {
    const { user, request, intents } = await setup();
    await user.click(screen.getByRole("button", { name: "Add files" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("knowledge", "addFiles", []));
    expect(intents).toContainEqual(["knowledge", "addFiles", []]);
  });

  it("native Add folder uses a distinct picker intent without a path argument", async () => {
    const { user, request, intents } = await setup();
    await user.click(screen.getByRole("button", { name: "Add folder" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("knowledge", "addFolder", []));
    expect(intents).toContainEqual(["knowledge", "addFolder", []]);
  });

  it("import progress exposes current totals and release of controls after cancellation", async () => {
    const { emit } = await setup();
    const running = {
      jobId: "job-77", kind: "file", workspaceId: 5, status: "running",
      filesTotal: 8, filesProcessed: 3, bytesTotal: 100, bytesProcessed: 40,
      added: 2, updated: 1, duplicates: 0, unchanged: 0, skipped: 0,
      failedFiles: 0, lastFile: "chapter.md", error: "", warnings: [], cancelRequested: false,
    };
    emit("knowledge-ingest", { jobs: [running] });
    expect(screen.getByRole("progressbar", { name: "Files processed" })).toHaveAttribute("aria-valuenow", "3");
    expect(screen.getByText(/3\/8 files/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add folder" })).toBeDisabled();
    emit("knowledge-ingest", { jobs: [{ ...running, status: "cancelled", cancelRequested: true }] });
    expect(screen.queryByRole("button", { name: "Cancel import" })).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "Add files" })).toBeEnabled());
  });

  it("a completed query sends its typed text and default top-k, not source counts", async () => {
    const { user, intents } = await setup();
    await searchFor(user, "brown fox");
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "search", ["brown fox", 10]]));
    expect(intents.some((item) => item[1] === "search" && JSON.stringify(item[2]) === JSON.stringify(["brown fox", 1]))).toBe(false);
  });

  it("two retrieved hits are labelled search results, not sources used", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [RESULT, { ...RESULT, chunkId: 2, documentId: 11, documentTitle: "Second hit" }] } : {});
    await searchFor(user, "fox");
    expect(await screen.findByText("2 search results")).toBeInTheDocument();
    expect(screen.queryByText(/sources used/i)).toBeNull();
  });

  it("a result with only lexical rank displays Keyword without a Semantic badge", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [{ ...RESULT, rankSignals: { fts5: 4 } }] } : {});
    await searchFor(user, "fox");
    const signals = await screen.findByRole("group", { name: "Match signals" });
    expect(within(signals).getByText("Keyword #4")).toBeInTheDocument();
    expect(within(signals).queryByText(/Semantic/)).toBeNull();
  });

  it("a result with only vector rank displays Semantic without a Keyword badge", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [{ ...RESULT, rankSignals: { vector: 3 } }] } : {});
    await searchFor(user, "fox");
    const signals = await screen.findByRole("group", { name: "Match signals" });
    expect(within(signals).getByText("Semantic #3")).toBeInTheDocument();
    expect(within(signals).queryByText(/Keyword/)).toBeNull();
  });

  it("older search responses without rank signals do not fabricate match groups", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [{ ...RESULT, rankSignals: undefined }] } : {});
    await searchFor(user, "fox");
    await screen.findByText("Fox story");
    expect(screen.queryByRole("group", { name: "Match signals" })).toBeNull();
  });

  it("expanding the cited passage reveals the exact excerpt and source offsets", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [RESULT] } : {});
    await searchFor(user, "fox");
    const expand = await screen.findByRole("button", { name: "Show cited passage" });
    expect(screen.queryByRole("region", { name: "Cited passage for Fox story" })).toBeNull();
    await user.click(expand);
    const passage = screen.getByRole("region", { name: "Cited passage for Fox story" });
    expect(within(passage).getByText(RESULT.text)).toBeInTheDocument();
    expect(within(passage).getByText("Offset 0–44")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide cited passage" })).toHaveAttribute("aria-expanded", "true");
  });

  it("a genuine HTTPS source has a working external link", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [RESULT] } : {});
    await searchFor(user, "fox");
    const link = await screen.findByRole("link", { name: "Open source" });
    expect(link).toHaveAttribute("href", "https://example.com/fox");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("a local source path never creates a fake Open source URL", async () => {
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [{ ...RESULT, sourceUri: "/private/notes.txt", sourceFile: "/private/notes.txt" }] } : {});
    await searchFor(user, "fox");
    await screen.findByText("Fox story");
    expect(screen.queryByRole("link", { name: "Open source" })).toBeNull();
  });

  it("search renders browser-safe names instead of private Windows and POSIX upload paths", async () => {
    const windows = String.raw`C:\Users\Ada\.graphlink\uploads\secret\guide.md`;
    const posix = "/home/ada/.graphlink/knowledge/uploads/secret/notes.md";
    const { user, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "search" ? { results: [
      { ...RESULT, sourceUri: windows, sourceFile: windows, documentTitle: "Guide" },
      { ...RESULT, chunkId: 2, documentId: 11, sourceUri: posix, sourceFile: posix, documentTitle: "Notes" },
    ] } : {});
    await searchFor(user, "fox");
    expect(await screen.findByText("2 search results")).toBeInTheDocument();
    expect(screen.getByText("guide.md")).toBeInTheDocument();
    expect(screen.getByText("notes.md")).toBeInTheDocument();
    expect(screen.queryByText(windows)).toBeNull();
    expect(screen.queryByText(posix)).toBeNull();
  });

  it("failed searches show an alert rather than empty results", async () => {
    const { user, request } = await setup();
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "search") throw new Error("offline");
      return {};
    });
    try {
      await searchFor(user, "fox");
      expect(await screen.findByRole("alert")).toHaveTextContent(/Search failed/);
      expect(screen.queryByText("No search results")).toBeNull();
      expect(log).toHaveBeenCalled();
    } finally { log.mockRestore(); }
  });

  it("the Search button is disabled until the query has non-whitespace text", async () => {
    const { user } = await setup();
    const button = screen.getByRole("button", { name: "Search" });
    expect(button).toBeDisabled();
    await searchFor(user, "   ");
    expect(button).toBeDisabled();
    await user.type(screen.getByRole("searchbox", { name: "Search the knowledge base" }), "fox");
    expect(button).toBeEnabled();
  });

  it("Enter submits search without a duplicate request from the live debounce", async () => {
    const { user, intents } = await setup();
    await searchFor(user, "fox{Enter}");
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "search", ["fox", 10]]));
    expect(intents.filter((entry) => entry[1] === "search")).toHaveLength(1);
  });

  it("workspace documents show type, formatted size, chunk count, date, and indexed status", async () => {
    await setup({ documents: [DOC] });
    const item = await screen.findByText("report.csv");
    const row = item.closest("li");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText(/CSV · 2\.0 KB · 4 chunks · Added/)).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("Indexed")).toBeInTheDocument();
  });


  it("branch-library notifications refresh an open document list", async () => {
    const { emit, request } = await setup();
    request.mockImplementation(async (_topic, intent) => intent === "listDocuments"
      ? { documents: [{ ...DOC, documentId: 71, title: "Branch (node n1)", type: "text/branch", canReindex: false }] }
      : intent === "listWatchedFolders" ? { folders: [] } : {});
    emit("knowledge-library", { workspaceId: 5 });
    const title = await screen.findByText("Branch (node n1)");
    expect(within(title.closest("li") as HTMLElement).getByText(/Chat branch ·/)).toBeInTheDocument();
  });

  it("indexed chats and branches display readable kinds and their actual stored sizes", async () => {
    await setup({ documents: [
      { ...DOC, documentId: 11, title: "Saved chat", type: "text/graph", sizeBytes: 1536, canReindex: false },
      { ...DOC, documentId: 12, title: "Saved branch", type: "text/branch", sizeBytes: 128, canReindex: false },
    ] });
    const chat = (await screen.findByText("Saved chat")).closest("li");
    const branch = screen.getByText("Saved branch").closest("li");
    expect(within(chat as HTMLElement).getByText(/Chat · 1\.5 KB/)).toBeInTheDocument();
    expect(within(branch as HTMLElement).getByText(/Chat branch · 128 B/)).toBeInTheDocument();
    expect(screen.queryByText("text/graph")).toBeNull();
  });

  it("Library document title tooltip cannot expose its private source path", async () => {
    const privatePath = String.raw`C:\Users\Ada\.graphlink\knowledge\uploads\secret\report.csv`;
    await setup({ documents: [{ ...DOC, sourceUri: privatePath }] });
    const title = await screen.findByText("report.csv");
    expect(title.getAttribute("title")).not.toBe(privatePath);
    expect(screen.queryByText(privatePath)).toBeNull();
  });

  it("removing a document requires confirmation and leaves it intact when declined", async () => {
    const { user, intents } = await setup({ documents: [DOC] });
    await screen.findByText("report.csv");
    await chooseDocumentAction(user, "report.csv", "Remove…");
    const confirm = await screen.findByRole("dialog", { name: "Remove document?" });
    expect(within(confirm).getByText(/report.csv/)).toBeInTheDocument();
    expect(intents.filter((item) => item[1] === "removeDocument")).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("report.csv")).toBeInTheDocument();
    expect(intents.filter((item) => item[1] === "removeDocument")).toHaveLength(0);
  });

  it("document re-index only sends its indexed id, never a client file path", async () => {
    const { user, intents } = await setup({ documents: [DOC] });
    await screen.findByText("report.csv");
    await chooseDocumentAction(user, "report.csv", "Re-index");
    expect(intents).toContainEqual(["knowledge", "reindexDocument", [10]]);
    expect(intents.some((item) => item[1] === "reindexDocument" && JSON.stringify(item[2]).includes("Users"))).toBe(false);
  });

  it("re-indexing without a local source stays disabled in the document menu", async () => {
    const { user, intents } = await setup({ documents: [{ ...DOC, canReindex: false, sourceUri: "https://example.org/report" }] });
    await screen.findByText("report.csv");
    await user.click(documentActions("report.csv").getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Re-index" })).toBeDisabled();
    expect(intents.some((item) => item[1] === "reindexDocument")).toBe(false);
  });

  it("browser file drops send bounded begin/append/finish upload intents", async () => {
    const { request, intents } = await setup();
    const bytes = new TextEncoder().encode("hello markdown");
    const file = new File([bytes], "dragged.md", { type: "text/markdown" });
    Object.defineProperty(file, "slice", {
      value: () => ({ arrayBuffer: async () => bytes.buffer }),
    });
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "beginUpload") return { uploadId: "upload-33" };
      if (intent === "listDocuments") return { documents: [] };
      if (intent === "listWatchedFolders") return { folders: [] };
      return {};
    });
    fireEvent.drop(screen.getByRole("region", { name: "Drop files to index" }), {
      dataTransfer: { files: [file] },
    });
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "finishUpload", ["upload-33"]]));
    expect(intents).toContainEqual(["knowledge", "beginUpload", [[{ name: "dragged.md", size: bytes.length }]]]);
    expect(intents.some((entry) => entry[1] === "appendUpload" && (entry[2] as unknown[] | undefined)?.[0] === "upload-33")).toBe(true);
    expect(intents.some((entry) => JSON.stringify(entry[2]).includes("C:\\Users\\"))).toBe(false);
  });

  it("oversized browser drops are rejected before beginning any upload", async () => {
    const { intents } = await setup();
    const file = new File(["test"], "too-large.md", { type: "text/markdown" });
    Object.defineProperty(file, "size", { value: 26 * 1024 * 1024 });
    fireEvent.drop(screen.getByRole("region", { name: "Drop files to index" }), {
      dataTransfer: { files: [file] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(/Drop limit/);
    expect(intents.some((entry) => entry[1] === "beginUpload")).toBe(false);
  });

  it("labelled search and embedding setup remain accessible in the same dialog", async () => {
    const { user, emit } = await setup();
    const dialog = screen.getByRole("dialog", { name: "Knowledge library" });
    expect(within(dialog).getByRole("searchbox", { name: "Search the knowledge base" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Embeddings" }));
    emit("app-settings", { knowledgeEmbeddingSource: "off", knowledgeEmbeddingModel: "" });
    expect(within(dialog).getByRole("button", { name: "Set up embeddings" })).toBeInTheDocument();
  });

  it("watched folders show persisted file totals, last sync, job counts, and errors", async () => {
    const folder = { ...FOLDER, fileCount: 7, lastError: "2 files skipped",
      lastJob: { filesTotal: 8, filesProcessed: 8, added: 2, updated: 1,
        removed: 0, skipped: 2, failedFiles: 1, error: "" } };
    const { user } = await setup({ folders: [folder] });
    await user.click(screen.getByRole("tab", { name: "Watched folders (1)" }));
    expect(await screen.findByText(folder.path)).toBeInTheDocument();
    expect(screen.getByText(/7 indexed · Last sync:/)).toBeInTheDocument();
    expect(screen.getByText(new Date(folder.lastSyncAt).toLocaleString())).toBeInTheDocument();
    expect(screen.getByText(/8\/8 scanned · 2 added · 1 updated · 0 removed · 1 failed/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("2 files skipped");
  });

  it("Enter on Add watched folder calls the native picker only, without leaking its path", async () => {
    const { user, intents, request } = await setup();
    let folders: Array<Record<string, unknown>> = [];
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listWatchedFolders") return { folders };
      if (intent === "addWatchedFolder") {
        folders = [{ ...FOLDER, fileCount: 0, lastSyncAt: null, syncStatus: "queued" }];
        return { folder: folders[0] };
      }
      if (intent === "listDocuments") return { documents: [] };
      return {};
    });
    await user.click(screen.getByRole("tab", { name: "Watched folders (0)" }));
    const add = screen.getByRole("button", { name: "Add watched folder" });
    add.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "addWatchedFolder", []]));
    expect(intents).not.toContainEqual(["knowledge", "addWatchedFolder", [FOLDER.path]]);
    expect(await screen.findByText(FOLDER.path)).toBeInTheDocument();
    expect(screen.getByText(/0 indexed · Last sync: Never/)).toBeInTheDocument();
  });

  it("canceling the native watch picker adds no folder and leaves the EmptyState", async () => {
    const { user, request, intents } = await setup();
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "addWatchedFolder") return { cancelled: true };
      if (intent === "listWatchedFolders") return { folders: [] };
      if (intent === "listDocuments") return { documents: [] };
      return {};
    });
    await user.click(screen.getByRole("tab", { name: "Watched folders (0)" }));
    await user.click(screen.getByRole("button", { name: "Add watched folder" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Add watched folder" })).toBeEnabled());
    expect(screen.getByText("No watched folders yet")).toBeInTheDocument();
    expect(intents).toContainEqual(["knowledge", "addWatchedFolder", []]);
  });

  it("keyboard actions sync one watch by id and sync all with no arguments", async () => {
    const { user, intents } = await setup({ folders: [FOLDER] });
    await user.click(screen.getByRole("tab", { name: "Watched folders (1)" }));
    await user.click(screen.getByRole("button", { name: "Actions" }));
    const syncOne = await screen.findByRole("menuitem", { name: "Sync now" });
    syncOne.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "syncWatchedFolders", [7]]));
    const syncAll = screen.getByRole("button", { name: "Sync all now" });
    await waitFor(() => expect(syncAll).toBeEnabled());
    syncAll.focus();
    await user.keyboard(" ");
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "syncWatchedFolders", []]));
  });

  it("watch removal can be cancelled and never deletes indexed documents", async () => {
    const { user, intents, request } = await setup({ documents: [DOC], folders: [FOLDER] });
    let folders: Array<Record<string, unknown>> = [FOLDER];
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "listWatchedFolders") return { folders };
      if (intent === "listDocuments") return { documents: [DOC] };
      if (intent === "removeWatchedFolder") { folders = []; return { removed: true }; }
      return {};
    });
    await user.click(screen.getByRole("tab", { name: "Watched folders (1)" }));
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove watched folder…" }));
    const confirmation = await screen.findByRole("dialog", { name: "Stop watching folder?" });
    expect(within(confirmation).getByText(/Indexed documents will be kept/)).toBeInTheDocument();
    await user.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    expect(intents.some((entry) => entry[1] === "removeWatchedFolder")).toBe(false);
    expect(screen.getByText(FOLDER.path)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove watched folder…" }));
    await user.click(within(await screen.findByRole("dialog", { name: "Stop watching folder?" }))
      .getByRole("button", { name: "Remove watched folder" }));
    await waitFor(() => expect(intents).toContainEqual(["knowledge", "removeWatchedFolder", [7]]));
    expect(await screen.findByText("No watched folders yet")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Documents (1)" }));
    expect(screen.getByText("report.csv")).toBeInTheDocument();
    expect(intents.some((entry) => entry[1] === "removeDocument")).toBe(false);
  });

  it("watch sync failures show a visible alert without removing the watch", async () => {
    const { user, request } = await setup({ folders: [FOLDER] });
    request.mockImplementation(async (_topic, intent) => {
      if (intent === "syncWatchedFolders") throw new Error("Sync denied.");
      if (intent === "listWatchedFolders") return { folders: [FOLDER] };
      if (intent === "listDocuments") return { documents: [] };
      return {};
    });
    await user.click(screen.getByRole("tab", { name: "Watched folders (1)" }));
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sync now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sync denied.");
    expect(screen.getByText(FOLDER.path)).toBeInTheDocument();
  });

  it("a second Enter on an unchanged in-flight search never duplicates its request", async () => {
    let resolveSearch!: (payload: unknown) => void;
    const { user, request, intents } = await setup();
    request.mockImplementation((_topic, intent) => intent === "search"
      ? new Promise((resolve) => { resolveSearch = resolve; })
      : Promise.resolve({}));
    await searchFor(user, "fox{Enter}");
    await waitFor(() => expect(intents.filter((entry) => entry[1] === "search")).toHaveLength(1));
    await user.keyboard("{Enter}");
    expect(intents.filter((entry) => entry[1] === "search")).toHaveLength(1);
    await act(async () => { resolveSearch({ results: [RESULT] }); });
    expect(await screen.findByText("Fox story")).toBeInTheDocument();
  });

  it("5,000 documents show only 50 interactive rows per page with keyboard paging", async () => {
    const documents = Array.from({ length: 5000 }, (_, index) => ({
      ...DOC, documentId: index + 1, title: "doc-" + (index + 1) + ".md",
    }));
    const { user } = await setup({ documents });
    const library = screen.getByRole("region", { name: "Workspace documents" });
    await within(library).findByText("doc-1.md");
    expect(within(library).getAllByRole("listitem")).toHaveLength(50);
    expect(within(library).queryByText("doc-51.md")).toBeNull();
    expect(within(library).getByRole("status")).toHaveTextContent("Showing 1–50 of 5000");
    const next = within(library).getByRole("button", { name: "Next documents" });
    next.focus();
    await user.keyboard("{Enter}");
    expect(within(library).getAllByRole("listitem")).toHaveLength(50);
    expect(within(library).getByText("doc-51.md")).toBeInTheDocument();
    expect(within(library).queryByText("doc-1.md")).toBeNull();
    const previous = within(library).getByRole("button", { name: "Previous documents" });
    previous.focus();
    await user.keyboard(" ");
    expect(within(library).getByText("doc-1.md")).toBeInTheDocument();
    expect(previous).toBeDisabled();
  });

  it("document removal focuses the confirmation and restores its menu trigger on Cancel", async () => {
    const { user } = await setup({ documents: [DOC] });
    await screen.findByText("report.csv");
    const action = documentActions("report.csv").getByRole("button", { name: "Actions" });
    await user.click(action);
    await user.click(await screen.findByRole("menuitem", { name: "Remove…" }));
    const confirmation = await screen.findByRole("dialog", { name: "Remove document?" });
    expect(within(confirmation).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(action).toHaveFocus());
  });

  it("verified citations open Document View with intact passage offsets", async () => {
    const onOpenDocumentView = vi.fn();
    const { user, request } = await setup({ onOpenDocumentView });
    request.mockImplementation(async (_topic, intent) => intent === "search" ? { results: [RESULT] } : {});
    await searchFor(user, "fox");
    await screen.findByText("Fox story");
    const open = screen.getByRole("button", { name: "Open in Document View" });
    open.focus();
    await user.keyboard("{Enter}");
    expect(onOpenDocumentView).toHaveBeenCalledWith("", "Fox story", expect.objectContaining({
      citationId: RESULT.citationId, excerpt: RESULT.text, section: "Opening",
      documentId: 10, chunkId: 1, offsetStart: 0, offsetEnd: 44, cited: true,
    }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Knowledge library" })).toBeNull());
  });

  it("unverified citations never offer Document View navigation or an openDocument fallback", async () => {
    const onOpenDocumentView = vi.fn();
    const { user, request, intents } = await setup({ onOpenDocumentView });
    request.mockImplementation(async (_topic, intent) => intent === "search"
      ? { results: [{ ...RESULT, citationId: "not-verified" }] } : {});
    await searchFor(user, "fox");
    await screen.findByText("Fox story");
    expect(screen.queryByRole("button", { name: "Open in Document View" })).toBeNull();
    expect(onOpenDocumentView).not.toHaveBeenCalled();
    expect(intents.some((entry) => entry[1] === "openDocument")).toBe(false);
  });

});
