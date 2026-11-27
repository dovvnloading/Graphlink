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
import { act, fireEvent, render, screen } from "@testing-library/react";
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
import { makeRequestOnlyTransport as makeTransport } from "../../lib/ws/transport.testUtils";

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

    expect(intents).toContainEqual(["globalSearch", "search", ["secret phrase", 10, null, "all"]]);
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

  it.each(["click", "Enter"])("retries a failed search via %s without changing its query", async (method) => {
    const { request } = await setup();
    vi.useFakeTimers();
    try {
      request.mockRejectedValueOnce(new Error("offline"));
      let resolveRetry: (value: unknown) => void = () => {};
      request.mockImplementationOnce(() => new Promise((resolve) => { resolveRetry = resolve; }));
      const input = screen.getByPlaceholderText(/search every workspace/i);
      const search = screen.getByRole("button", { name: "Search" });

      fireEvent.change(input, { target: { value: "query" } });
      await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
      expect(screen.getByRole("alert")).toHaveTextContent(/search failed/i);
      expect(search).toBeEnabled();

      // The debounce from typing must not turn a failure into an implicit retry.
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(request).toHaveBeenCalledTimes(1);

      await act(async () => {
        if (method === "click") fireEvent.click(search);
        else fireEvent.keyDown(input, { key: "Enter" });
      });
      expect(request).toHaveBeenCalledTimes(2);
      expect(request).toHaveBeenNthCalledWith(1, "globalSearch", "search", ["query", 10, null, "all"]);
      expect(request).toHaveBeenNthCalledWith(2, "globalSearch", "search", ["query", 10, null, "all"]);
      expect(input).toHaveValue("query");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(search).toBeDisabled();
      expect(search).toHaveAttribute("aria-busy", "true");

      // Keyboard submission must also respect the in-flight guard.
      fireEvent.keyDown(input, { key: "Enter" });
      expect(request).toHaveBeenCalledTimes(2);

      await act(async () => { resolveRetry({ results: [DOCUMENT_HIT] }); });
      expect(screen.getByText("Fox Story")).toBeInTheDocument();
      expect(screen.getByText("1 result found")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(search).toBeEnabled();

      fireEvent.click(search);
      expect(request).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps repeated failures recoverable after a debounced search in a non-default scope", async () => {
    const { request } = await setup();
    vi.useFakeTimers();
    try {
      request.mockRejectedValueOnce(new Error("offline"));
      request.mockRejectedValueOnce(new Error("still offline"));
      request.mockResolvedValueOnce({ results: [DOCUMENT_HIT] });
      const input = screen.getByPlaceholderText(/search every workspace/i);
      const search = screen.getByRole("button", { name: "Search" });

      fireEvent.click(screen.getByRole("radio", { name: "Knowledge" }));
      fireEvent.change(input, { target: { value: "query" } });
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(request).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("alert")).toHaveTextContent(/search failed/i);

      await act(async () => { fireEvent.click(search); });
      expect(request).toHaveBeenCalledTimes(2);
      expect(screen.getByRole("alert")).toHaveTextContent(/search failed/i);
      expect(search).toBeEnabled();

      await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
      expect(request.mock.calls).toEqual([
        ["globalSearch", "search", ["query", 10, null, "knowledge"]],
        ["globalSearch", "search", ["query", 10, null, "knowledge"]],
        ["globalSearch", "search", ["query", 10, null, "knowledge"]],
      ]);
      expect(screen.getByText("Fox Story")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["query", "scope"])("ignores a stale rejection after a %s change", async (changed) => {
    const { request } = await setup();
    vi.useFakeTimers();
    try {
      let rejectOld: (reason: Error) => void = () => {};
      let resolveNew: (value: unknown) => void = () => {};
      request.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject; }));
      request.mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve; }));
      const input = screen.getByPlaceholderText(/search every workspace/i);
      const search = screen.getByRole("button", { name: "Search" });

      fireEvent.change(input, { target: { value: "query" } });
      fireEvent.keyDown(input, { key: "Enter" });
      if (changed === "query") {
        fireEvent.change(input, { target: { value: "new query" } });
        fireEvent.keyDown(input, { key: "Enter" });
      } else {
        fireEvent.click(screen.getByRole("radio", { name: "Graphs" }));
      }
      expect(request).toHaveBeenCalledTimes(2);
      expect(request).toHaveBeenNthCalledWith(2, "globalSearch", "search",
        [changed === "query" ? "new query" : "query", 10, null, changed === "scope" ? "graphs" : "all"]);

      await act(async () => { rejectOld(new Error("stale failure")); });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(search).toBeDisabled();

      fireEvent.keyDown(input, { key: "Enter" });
      await act(async () => { await vi.advanceTimersByTimeAsync(250); });
      expect(request).toHaveBeenCalledTimes(2);

      await act(async () => { resolveNew({ results: [GRAPH_HIT] }); });
      expect(screen.getByText("Workspace B / Onboarding Notes")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(search).toBeEnabled();

      fireEvent.click(search);
      expect(request).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a newer query can run while an older reply is in flight; stale results are ignored", async () => {
    const { user, request } = await setup();
    let resolveFirst: (value: unknown) => void = () => {};
    let resolveSecond: (value: unknown) => void = () => {};
    request.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    request.mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    const input = screen.getByPlaceholderText(/search every workspace/i);
    await user.type(input, "first query{Enter}");
    expect(request).toHaveBeenCalledTimes(1);
    await user.clear(input);
    await user.type(input, "second query{Enter}");
    expect(request).toHaveBeenCalledTimes(2);

    resolveFirst({ results: [DOCUMENT_HIT] });
    expect(screen.queryByText("Fox Story")).toBeNull();
    resolveSecond({ results: [GRAPH_HIT] });
    expect(await screen.findByText("Workspace B / Onboarding Notes")).toBeInTheDocument();
  });

  it("re-queries the same text on each scope switch, even when All omits that kind", async () => {
    const { user, request, intents } = await setup();
    request.mockResolvedValueOnce({ results: [DOCUMENT_HIT] });
    request.mockResolvedValueOnce({ results: [GRAPH_HIT] });
    request.mockResolvedValueOnce({ results: [DOCUMENT_HIT] });
    request.mockResolvedValueOnce({ results: [DOCUMENT_HIT, GRAPH_HIT] });
    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query{Enter}");
    expect(await screen.findByText("Fox Story")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "Graphs" }));
    expect(await screen.findByText("Workspace B / Onboarding Notes")).toBeInTheDocument();
    expect(screen.queryByText("Fox Story")).toBeNull();
    expect(intents).toContainEqual(["globalSearch", "search", ["query", 10, null, "graphs"]]);

    await user.click(screen.getByRole("radio", { name: "Knowledge" }));
    expect(await screen.findByText("Fox Story")).toBeInTheDocument();
    expect(screen.queryByText("Workspace B / Onboarding Notes")).toBeNull();
    expect(intents).toContainEqual(["globalSearch", "search", ["query", 10, null, "knowledge"]]);

    await user.click(screen.getByRole("radio", { name: "All" }));
    expect(await screen.findByText("2 results found")).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(4);
  });

  it("ignores an in-flight reply from the previous scope", async () => {
    const { user, request } = await setup();
    let resolveOld: (value: unknown) => void = () => {};
    let resolveNew: (value: unknown) => void = () => {};
    request.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    request.mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve; }));
    await user.type(screen.getByPlaceholderText(/search every workspace/i), "query{Enter}");
    await user.click(screen.getByRole("radio", { name: "Graphs" }));
    expect(request).toHaveBeenCalledTimes(2);

    await act(async () => { resolveNew({ results: [GRAPH_HIT] }); });
    expect(await screen.findByText("Workspace B / Onboarding Notes")).toBeInTheDocument();
    await act(async () => { resolveOld({ results: [DOCUMENT_HIT] }); });
    expect(screen.queryByText("Fox Story")).toBeNull();
    expect(screen.getByText("Workspace B / Onboarding Notes")).toBeInTheDocument();
  });
});
