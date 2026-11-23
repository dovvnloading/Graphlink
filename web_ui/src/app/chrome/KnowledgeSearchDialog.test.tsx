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
});
