import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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

function makeTransport() {
  let listener: ((payload: Record<string, unknown>) => void) | null = null;
  const transport = {
    subscribe: (_topic: string, l: (payload: Record<string, unknown>) => void) => {
      listener = l;
      return () => {
        listener = null;
      };
    },
  } as unknown as WsTransport;
  return {
    transport,
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
});
