import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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

async function setup() {
  const user = userEvent.setup();
  const fake = makeTransport();
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
      <KnowledgeSearchDialog transport={fake.transport} />
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

  it("the search input has an accessible label", async () => {
    await setup();
    expect(screen.getByLabelText("Search the knowledge base")).toBeInTheDocument();
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
    expect(request).toHaveBeenCalledTimes(1);

    await user.clear(input);
    await user.type(input, "second query{Enter}");
    expect(request).toHaveBeenCalledTimes(1);

    resolveFirst({ results: [RESULT_A] });
    expect(await screen.findByText("Fox Story")).toBeInTheDocument();
  });
});
