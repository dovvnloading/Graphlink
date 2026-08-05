import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WsTransport } from "../../lib/ws/transport";
import { ExecutionLimitsProvider, useExecutionLimits } from "./ExecutionLimitsContext";

type StateListener = (payload: Record<string, unknown>) => void;

function makeFakeTransport() {
  const listeners = new Map<string, StateListener>();
  const transport = {
    subscribe: vi.fn((topic: string, listener: StateListener) => {
      listeners.set(topic, listener);
      return () => listeners.delete(topic);
    }),
  } as unknown as WsTransport;
  return { transport, listeners };
}

function validPayload(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 1,
    pycoderResourceLimitsText: "Execution is capped at approximately 2 GB of memory.",
    codeSandboxResourceLimitsText: "Execution is capped at approximately 2 GB of memory. Binary only.",
    ...overrides,
  };
}

function Consumer() {
  const state = useExecutionLimits();
  return (
    <>
      <span data-testid="pycoder">{state.pycoderResourceLimitsText}</span>
      <span data-testid="code-sandbox">{state.codeSandboxResourceLimitsText}</span>
    </>
  );
}

describe("ExecutionLimitsProvider / useExecutionLimits", () => {
  it("subscribes to the execution-limits topic on mount", () => {
    const { transport, listeners } = makeFakeTransport();
    render(
      <ExecutionLimitsProvider transport={transport}>
        <Consumer />
      </ExecutionLimitsProvider>,
    );
    expect(transport.subscribe).toHaveBeenCalledWith("execution-limits", expect.any(Function));
    expect(listeners.has("execution-limits")).toBe(true);
  });

  it("starts with blank text before any snapshot arrives", () => {
    const { transport } = makeFakeTransport();
    render(
      <ExecutionLimitsProvider transport={transport}>
        <Consumer />
      </ExecutionLimitsProvider>,
    );
    expect(screen.getByTestId("pycoder")).toHaveTextContent("");
    expect(screen.getByTestId("code-sandbox")).toHaveTextContent("");
  });

  it("exposes a valid snapshot's fields to consumers", () => {
    const { transport, listeners } = makeFakeTransport();
    render(
      <ExecutionLimitsProvider transport={transport}>
        <Consumer />
      </ExecutionLimitsProvider>,
    );
    act(() => listeners.get("execution-limits")!(validPayload()));
    expect(screen.getByTestId("pycoder")).toHaveTextContent(
      "Execution is capped at approximately 2 GB of memory.",
    );
    expect(screen.getByTestId("code-sandbox")).toHaveTextContent(
      "Execution is capped at approximately 2 GB of memory. Binary only.",
    );
  });

  it("rejects a malformed snapshot and keeps the previous state, logging an error", () => {
    const { transport, listeners } = makeFakeTransport();
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ExecutionLimitsProvider transport={transport}>
        <Consumer />
      </ExecutionLimitsProvider>,
    );
    act(() => listeners.get("execution-limits")!(validPayload()));
    // Missing the required pycoderResourceLimitsText field entirely.
    const { pycoderResourceLimitsText: _omit, ...malformed } = validPayload();
    act(() => listeners.get("execution-limits")!(malformed));

    expect(screen.getByTestId("pycoder")).toHaveTextContent(
      "Execution is capped at approximately 2 GB of memory.",
    );
    expect(errorSpy).toHaveBeenCalledWith(
      "[execution-limits] rejected snapshot:",
      expect.anything(),
    );
    errorSpy.mockRestore();
  });

  it("useExecutionLimits outside any Provider falls back to blank text rather than throwing", () => {
    // No ExecutionLimitsProvider ancestor at all - mirrors
    // CodeExecutionApprovalPanel.test.tsx's own standalone render style.
    render(<Consumer />);
    expect(screen.getByTestId("pycoder")).toHaveTextContent("");
    expect(screen.getByTestId("code-sandbox")).toHaveTextContent("");
  });
});
