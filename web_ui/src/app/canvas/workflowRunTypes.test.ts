import { describe, expect, it } from "vitest";
import { alignWorkflowSteps, changedWorkflowFields, type WorkflowRunDetail, type WorkflowStepReceipt } from "./workflowRunTypes";

function step(nodeId: string, index: number, status = "completed"): WorkflowStepReceipt {
  return {
    step_index: index, nodeId, sourceIds: ["source"], kind: "model", operation: "model", retryable: true,
    status, started_at: null, finished_at: null, duration_ms: null, provider: "Ollama", model: "model",
    input_tokens: 3, output_tokens: 2, total_tokens: 5, cost_usd: 0, approval: "reviewed", approval_id: null,
    retry_count: 0, request_id: null, attempt_phase: null,
    input_ref: { nodeIds: ["source"], sha256: "input", configuration: "config" },
    output_ref: { nodeId, sha256: "result", version: null },
  };
}
function run(steps: WorkflowStepReceipt[], retained = true): WorkflowRunDetail {
  return { graph_id: 1, run_id: "run", started_at: "", finished_at: null, duration_ms: null,
    status: "completed", max_concurrency: 3, steps, events: [], retained };
}

describe("workflow step comparison", () => {
  it("matches remapped live node identity independently of index", () => {
    const a = step("a", 0), b = step("b", 1);
    const b2 = step("b", 0, "failed"), c = step("c", 1);
    const pairs = alignWorkflowSteps(run([a, b]), run([b2, c]));
    expect(pairs).toEqual([{ first: a, second: null }, { first: b, second: b2 }, { first: null, second: c }]);
    expect(changedWorkflowFields(b, b2)).toEqual(["Status"]);
  });
  it("uses position and operation for pruned receipts", () => {
    const a = step("old", 0), b = step("new", 0);
    expect(alignWorkflowSteps(run([a], false), run([b]))).toEqual([{ first: a, second: b }]);
    expect(changedWorkflowFields(a, b)).toEqual([]);
    expect(changedWorkflowFields(a, null)).toEqual(["Removed"]);
  });
});
