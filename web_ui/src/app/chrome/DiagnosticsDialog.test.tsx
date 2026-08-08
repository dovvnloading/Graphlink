import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DiagnosticsDialog } from "./DiagnosticsDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

const snapshot = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  recentRuns: [
    { runId: "r-2", kind: "chat", nodeId: "n-2", outcome: "running", durationSeconds: null },
    { runId: "r-1", kind: "chat", nodeId: "n-1", outcome: "completed", durationSeconds: 1.5 },
  ],
  publishCount: 42,
  publishBytesTotal: 2048,
  lastPublishBytes: 512,
  lastPublishTopic: "scene",
  publishBytesPerSecond: 100.5,
  sessionCount: 3,
  providerErrors: [{ provider: "ollama", message: "connection refused", at: 1700000000 }],
};

// Matches ChatLibraryDialog.test.tsx's exact makeTransport() shape
// (intents array + intent/fireIntent recording into it) - PLUS a `request`
// mock, since exportDiagnosticBundle needs the actual reply
// (fireIntent()/intent() are both `: void` on the real transport, see
// transport.ts and DiagnosticsDialog.tsx's own exportDiagnosticBundle() doc
// for why that call goes through transport.request() instead). `request` is
// wired to also record into the same `intents` array so a single assertion
// list covers all three call shapes.
function makeTransport() {
  const intents: unknown[][] = [];
  let listener: ((payload: Record<string, unknown>) => void) | null = null;
  const request = vi.fn<(topic: string, intent: string, args?: unknown[]) => Promise<unknown>>();
  const transport = {
    subscribe: (_topic: string, l: (payload: Record<string, unknown>) => void) => {
      listener = l;
      return () => {
        listener = null;
      };
    },
    intent: (topic: string, intent: string, args: unknown[]) => {
      intents.push([topic, intent, args]);
    },
    fireIntent: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
    },
    request: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
      return request(topic, intent, args);
    },
  } as unknown as WsTransport;
  return {
    transport,
    intents,
    request,
    push: (payload: Record<string, unknown>) => listener?.(payload),
  };
}

function OpenDiagnosticsButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("diagnostics", "dialog")}>
      open diagnostics
    </button>
  );
}

async function setup() {
  const user = userEvent.setup();
  const fake = makeTransport();
  render(
    <OverlayProvider>
      <OpenDiagnosticsButton />
      <DiagnosticsDialog transport={fake.transport} />
    </OverlayProvider>,
  );
  act(() => fake.push(snapshot));
  await user.click(screen.getByRole("button", { name: "open diagnostics" }));
  return { user, ...fake };
}

describe("DiagnosticsDialog", () => {
  it("renders publish size/rate and session count from the live snapshot", async () => {
    await setup();
    expect(screen.getByText("3")).toBeInTheDocument(); // sessionCount
    expect(screen.getByText("42")).toBeInTheDocument(); // publishCount
    expect(screen.getByText("2.0 KB")).toBeInTheDocument(); // publishBytesTotal
    expect(screen.getByText("scene · 512 B")).toBeInTheDocument(); // last publish
  });

  it("renders recent runs newest-first with outcome and duration", async () => {
    await setup();
    const rows = screen.getAllByRole("row").slice(1); // drop the header row
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("n-2");
    expect(rows[0]).toHaveTextContent("running");
    expect(rows[1]).toHaveTextContent("n-1");
    expect(rows[1]).toHaveTextContent("completed");
    expect(rows[1]).toHaveTextContent("1.50s");
  });

  it("renders a real provider error, matching the ADR-016 16.3 exit criterion", async () => {
    await setup();
    expect(screen.getByText("ollama")).toBeInTheDocument();
    expect(screen.getByText("connection refused")).toBeInTheDocument();
  });

  it("shows empty-state copy when there are no runs or provider errors yet", async () => {
    const user = userEvent.setup();
    const fake = makeTransport();
    render(
      <OverlayProvider>
        <OpenDiagnosticsButton />
        <DiagnosticsDialog transport={fake.transport} />
      </OverlayProvider>,
    );
    act(() =>
      fake.push({
        ...snapshot,
        recentRuns: [],
        providerErrors: [],
      }),
    );
    await user.click(screen.getByRole("button", { name: "open diagnostics" }));

    expect(screen.getByText("No runs yet this session.")).toBeInTheDocument();
    expect(screen.getByText("No provider errors this session.")).toBeInTheDocument();
  });

  it("clicking 'Open log folder' fires openLogFolder with no args (fire-and-forget, no reply awaited)", async () => {
    const { user, intents } = await setup();

    await user.click(screen.getByRole("button", { name: "Open log folder" }));

    expect(intents).toContainEqual(["diagnostics", "openLogFolder", []]);
  });

  it("clicking 'Export diagnostic bundle' requests exportDiagnosticBundle, then renders the bundle JSON and a working Copy button once it resolves", async () => {
    const bundle = {
      bundleSchemaVersion: 1,
      generatedAt: "2026-08-08T00:00:00Z",
      appVersion: "0.9.0",
      os: { system: "Windows", release: "11", pythonVersion: "3.12.4" },
      nodeCounts: { chat: 2, document: 1 },
      diagnostics: snapshot,
    };
    const path = "C:\\Users\\test\\.graphlink\\diagnostics\\bundle-20260808T000000Z.json";

    const { user, intents, request } = await setup();
    // userEvent.setup() (inside setup() above) installs its OWN
    // navigator.clipboard stub internally - defining this mock any earlier
    // gets silently clobbered the moment that runs (see DocumentViewPanel.
    // test.tsx's own "Copy button" describe block for the same fix).
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true, writable: true });
    request.mockResolvedValueOnce({ bundle, path });

    await user.click(screen.getByRole("button", { name: "Export diagnostic bundle" }));

    expect(intents).toContainEqual(["diagnostics", "exportDiagnosticBundle", []]);

    // The Copy button only renders once the export has resolved - a safe
    // readiness signal for the async state update. The path/JSON assertions
    // below go through querySelector + raw .textContent instead of
    // getByText: the caption's text is split across a static "Also written
    // to " text node and a sibling {path} expression (so the <p>'s own
    // textContent includes both, not path alone), and getByText also
    // normalizes whitespace (collapses newlines/indentation) before
    // comparing, which would falsely mismatch pretty-printed JSON's real
    // newlines.
    await screen.findByRole("button", { name: "Copy to clipboard" });

    const expectedJson = JSON.stringify(bundle, null, 2);
    const pre = document.querySelector(".diagnostics-bundle-preview");
    expect(pre?.textContent).toBe(expectedJson);
    expect(document.querySelector(".diagnostics-bundle-path")?.textContent).toBe(`Also written to ${path}`);

    await user.click(screen.getByRole("button", { name: "Copy to clipboard" }));
    expect(writeText).toHaveBeenCalledWith(expectedJson);
  });
});
