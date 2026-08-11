/**
 * ADR-020 stage 20.4. Mirrors two established precedents rather than
 * inventing test shape:
 * - KnowledgeSearchDialog.test.tsx's own makeTransport()/setup() (request()
 *   is the one transport method this dialog calls, never fireIntent/intent)
 *   and its monotonic-requestId stale-response-guard race.
 * - SceneCanvas.pinSearchJump.test.tsx's own useReactFlow wrapping (every
 *   real pan/zoom/getNodes export stays functional; only setCenter is
 *   intercepted) for asserting the jump-to-node call's exact arguments.
 */
import { ReactFlowProvider, type useReactFlow as UseReactFlowType } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

type SetCenterCall = [number, number, { zoom?: number; duration?: number } | undefined];
const setCenterCalls: SetCenterCall[] = [];

vi.mock("@xyflow/react", async (importOriginal) => {
  const original = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...original,
    useReactFlow: (...args: Parameters<typeof UseReactFlowType>) => {
      const real = original.useReactFlow(...args);
      return {
        ...real,
        setCenter: (x: number, y: number, options?: { zoom?: number; duration?: number }) => {
          setCenterCalls.push([x, y, options]);
          return real.setCenter(x, y, options);
        },
      };
    },
  };
});

import { GlobalSearchDialog } from "./GlobalSearchDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

// Matches KnowledgeSearchDialog.test.tsx's own makeTransport() shape exactly.
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

function OpenGlobalSearchButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("global-search", "dialog")}>
      open global search
    </button>
  );
}

function DialogOpenProbe() {
  const overlays = useOverlays();
  return <span data-testid="open-surface">{overlays.isOpen("global-search") ? "open" : "closed"}</span>;
}

async function setup() {
  const user = userEvent.setup();
  const fake = makeTransport();
  render(
    <OverlayProvider>
      <ReactFlowProvider>
        <OpenGlobalSearchButton />
        <DialogOpenProbe />
        <GlobalSearchDialog transport={fake.transport} />
      </ReactFlowProvider>
    </OverlayProvider>,
  );
  await user.click(screen.getByRole("button", { name: "open global search" }));
  return { user, ...fake };
}

const DOCUMENT_HIT = {
  chunkId: 1,
  documentId: 10,
  documentTitle: "Fox Story",
  sourceUri: "https://example.com/fox",
  text: "The quick brown fox jumps over the lazy dog.",
  offsetStart: 0,
  offsetEnd: 45,
  sourceNodeId: null,
  graphId: null,
};

const GRAPH_HIT = {
  chunkId: 2,
  documentId: 11,
  documentTitle: "Workspace B / Onboarding Notes",
  sourceUri: "graph:42",
  text: "The secret phrase lives only in this graph's node.",
  offsetStart: 0,
  offsetEnd: 51,
  sourceNodeId: "node-9",
  graphId: 42,
};

describe("GlobalSearchDialog", () => {
  beforeEach(() => {
    setCenterCalls.length = 0;
  });

  it("submitting a query requests search/globalSearch with the typed text and a default k", async () => {
    const { user, intents, request } = await setup();
    request.mockResolvedValueOnce({ results: [] });

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "secret phrase");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(intents).toContainEqual(["globalSearch", "search", ["secret phrase", 10]]);
  });

  it('renders "N results found" with N matching the real result count', async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [DOCUMENT_HIT, GRAPH_HIT] });

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("2 results found")).toBeInTheDocument();
  });

  it("shows an explicit empty state instead of a false zero-results count", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [] });

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "nothing here");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("No results found")).toBeInTheDocument();
  });

  it("a document hit (sourceNodeId/graphId both null) keeps the Open source link, not a jump action", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [DOCUMENT_HIT] });

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByText("Fox Story"));

    const link = screen.getByRole("link", { name: "Open source" });
    expect(link).toHaveAttribute("href", "https://example.com/fox");
    expect(screen.queryByRole("button", { name: "Jump to node" })).not.toBeInTheDocument();
  });

  it("a graph/node hit shows a Jump to node action instead of Open source", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [GRAPH_HIT] });

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByText("Workspace B / Onboarding Notes"));

    expect(screen.getByRole("button", { name: "Jump to node" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open source" })).not.toBeInTheDocument();
  });

  it("activating a graph/node hit fires loadGraphAndFocusNode with the right graphId/nodeId, then calls setCenter with the resolved coordinates and closes the dialog", async () => {
    const { user, intents, request } = await setup();
    request.mockResolvedValueOnce({ results: [GRAPH_HIT] });

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByText("Workspace B / Onboarding Notes"));

    request.mockResolvedValueOnce({ x: 1234, y: -567 });
    await user.click(screen.getByRole("button", { name: "Jump to node" }));

    expect(intents).toContainEqual(["app-chat-library", "loadGraphAndFocusNode", [42, "node-9"]]);
    expect(await screen.findByTestId("open-surface")).toHaveTextContent("closed");
    expect(setCenterCalls).toHaveLength(1);
    const [x, y, options] = setCenterCalls[0];
    expect(x).toBe(1234);
    expect(y).toBe(-567);
    expect(options?.zoom).toBe(1);
  });

  it("a stale node (loadGraphAndFocusNode resolves null) shows an honest error and never calls setCenter", async () => {
    const { user, request } = await setup();
    request.mockResolvedValueOnce({ results: [GRAPH_HIT] });

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(await screen.findByText("Workspace B / Onboarding Notes"));

    request.mockResolvedValueOnce(null);
    await user.click(screen.getByRole("button", { name: "Jump to node" }));

    expect(await screen.findByText("This node no longer exists in that graph.")).toBeInTheDocument();
    expect(setCenterCalls).toHaveLength(0);
  });

  it("a failed global search shows an error instead of a silent empty state", async () => {
    const { user, request } = await setup();
    request.mockRejectedValueOnce(new Error("boom"));

    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/search failed/i);
  });

  // Mirrors KnowledgeSearchDialog's own identical, already-adversarially-
  // reviewed test: runSearch()'s `searching` early-return is what actually
  // makes the monotonic-requestId guard below it unreachable in normal use
  // (both the Search button and Enter share the same gated function, so a
  // second dispatch while the first is still in flight is impossible to
  // trigger through the UI, not merely discarded after the fact) - proven
  // here the same way Knowledge proves it: a second Enter press while the
  // first request is still pending fires no second transport.request call
  // at all.
  it("a second Enter press while a search is already in flight does not fire a duplicate request", async () => {
    const { user, request } = await setup();
    let resolveFirst: (value: unknown) => void = () => {};
    request.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve; }),
    );

    const input = screen.getByPlaceholderText(/search every workspace/i);
    await user.type(input, "first query{Enter}");
    expect(request).toHaveBeenCalledTimes(1);

    await user.clear(input);
    await user.type(input, "second query{Enter}");
    expect(request).toHaveBeenCalledTimes(1);

    resolveFirst({ results: [GRAPH_HIT] });
    expect(await screen.findByText("Workspace B / Onboarding Notes")).toBeInTheDocument();
  });
});
