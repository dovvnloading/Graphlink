import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeSearchDialog } from "./KnowledgeSearchDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

// Matches DiagnosticsDialog.test.tsx's own makeTransport() shape exactly -
// KnowledgeSearchDialog only ever calls transport.request() (the "I need
// the actual return value" primitive), never fireIntent/intent.
function makeTransport() {
  const intents: unknown[][] = [];
  const request = vi.fn<(topic: string, intent: string, args?: unknown[]) => Promise<unknown>>();
  const transport = {
    subscribe: () => () => {},
    intent: () => {},
    fireIntent: () => {},
    request: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
      return request(topic, intent, args);
    },
  } as unknown as WsTransport;
  return { transport, intents, request };
}

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
  render(
    <OverlayProvider>
      <OpenKnowledgeButton />
      <KnowledgeSearchDialog transport={fake.transport} />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole("button", { name: "open knowledge" }));
  return { user, ...fake };
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
