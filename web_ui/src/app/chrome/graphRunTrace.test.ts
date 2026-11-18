import { describe, expect, it } from "vitest";
import type { GraphRunTrace } from "./graphRunTrace";
import { serializeGraphRunTrace } from "./graphRunTrace";

const SECRET_KEY = "DO_NOT_EXPORT_API_KEY_PRIVATE";
const SECRET_PROMPT = "DO_NOT_EXPORT_PROMPT_PRIVATE";
const SECRET_OUTPUT = "DO_NOT_EXPORT_OUTPUT_PRIVATE";

describe("Graph Runner JSON trace export", () => {
  it("serializes only approved metadata even when the supplied trace carries injected text", () => {
    const trace = {
      schemaVersion: 1, runId: "saved-run-1", status: "reconciliation-required",
      startedAt: "2026-11-18T09:00:00+00:00", finishedAt: null, durationMs: null,
      credential: SECRET_KEY, prompt: SECRET_PROMPT, response: SECRET_OUTPUT,
      steps: [{
        index: 1, sourceNodeId: "source", destinationNodeId: "destination",
        kind: "model", operation: "model", status: "reconciliation-required",
        provider: "Ollama", model: "qwen3:8b",
        label: SECRET_PROMPT, detail: SECRET_KEY, outputText: SECRET_OUTPUT,
        inputFingerprints: {
          input: "a".repeat(64), configuration: "b".repeat(64),
          destinationBefore: "c".repeat(64), endpoint: null,
          prompt: SECRET_PROMPT,
        },
        outputVersion: { sha256: null, number: null, output: SECRET_OUTPUT },
        blockedCause: "External request outcome is uncertain; reconcile before retrying.",
        startedAt: "2026-11-18T09:00:00+00:00",
        finishedAt: null, durationMs: null,
        attempts: [{
          requestId: "graph-" + "1".repeat(32), kind: "model",
          provider: "Ollama", model: "qwen3:8b", tool: null,
          inputFingerprint: "a".repeat(64),
          phase: "reconciliation-required", persistedPhase: "dispatched",
          dispatchedAt: "2026-11-18T09:00:00+00:00", resolvedAt: null,
          apiKey: SECRET_KEY, inputRef: SECRET_PROMPT, output: SECRET_OUTPUT,
        }],
      }],
    } as unknown as GraphRunTrace;

    const json = serializeGraphRunTrace(trace);
    for (const forbidden of [
      SECRET_KEY, SECRET_PROMPT, SECRET_OUTPUT, "credential", "outputText",
      "apiKey", "inputRef", '"prompt"', '"response"', '"label"', '"detail"',
    ]) {
      expect(json).not.toContain(forbidden);
    }
    const parsed = JSON.parse(json);
    expect(parsed.steps[0].attempts[0]).toMatchObject({
      requestId: "graph-" + "1".repeat(32),
      phase: "reconciliation-required", persistedPhase: "dispatched",
    });
    expect(parsed.steps[0].inputFingerprints.input).toBe("a".repeat(64));
    expect(parsed.steps[0].blockedCause).toMatch(/reconcile before retrying/);
    expect(Object.keys(parsed)).toEqual([
      "schemaVersion", "runId", "status", "startedAt", "finishedAt", "durationMs", "steps",
    ]);
    expect(Object.keys(parsed.steps[0])).not.toContain("detail");
  });

  it("rejects free-form injected metadata in fields that would be serialized", () => {
    const trace = {
      schemaVersion: 1, runId: "safe-run-2", status: "blocked",
      startedAt: SECRET_PROMPT, finishedAt: null, durationMs: null,
      steps: [{
        index: 1, sourceNodeId: "source", destinationNodeId: "destination",
        kind: "note", operation: "copy", status: "blocked",
        provider: SECRET_KEY, model: SECRET_PROMPT,
        inputFingerprints: { input: SECRET_OUTPUT, configuration: null, destinationBefore: null, endpoint: null },
        outputVersion: { sha256: SECRET_OUTPUT, number: -1 },
        blockedCause: SECRET_OUTPUT, startedAt: SECRET_KEY,
        finishedAt: null, durationMs: null, attempts: [],
      }],
    } as GraphRunTrace;
    const json = serializeGraphRunTrace(trace);
    expect(json).not.toContain(SECRET_KEY);
    expect(json).not.toContain(SECRET_PROMPT);
    expect(json).not.toContain(SECRET_OUTPUT);
    expect(JSON.parse(json).steps[0]).toMatchObject({
      provider: null, model: null,
      blockedCause: "The step was blocked by a saved validation failure.",
    });
  });
});
