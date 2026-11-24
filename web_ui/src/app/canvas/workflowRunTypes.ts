/** Journal receipts are metadata, never embedded prompt or result bodies. */
export type WorkflowScope = "through" | "from" | "failed";

export function workflowScope(value: string): WorkflowScope | undefined {
  return value === "through" || value === "from" || value === "failed" ? value : undefined;
}

export type WorkflowCanvasStatus = "Queued" | "Running" | "Waiting for approval" | "Skipped" | "Failed" | "Done" | "Cancelled";
export interface WorkflowCanvasState {
  statuses: Record<string, WorkflowCanvasStatus>;
  progress: { runId: string; total: number; finished: number } | null;
}

export interface WorkflowRunSummary {
  run_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  step_count: number;
  failed_count: number;
  total_tokens: number | null;
  cost_usd: number | null;
  priced_steps: number;
}

export interface WorkflowHistoryPage {
  graphId: number | null;
  runs: WorkflowRunSummary[];
  hasMore: boolean;
}

export interface WorkflowStepReceipt {
  step_index: number;
  kind: string;
  operation: string;
  nodeId: string | null;
  sourceIds: string[];
  retryable: boolean;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  provider: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  approval: string | null;
  approval_id: string | null;
  retry_count: number;
  request_id: string | null;
  attempt_phase: string | null;
  input_ref: { nodeIds: string[]; destinationNodeId?: string; sha256: string | null; configuration: string | null };
  output_ref: { nodeId: string; sha256: string; version: number | null } | null;
}

export interface WorkflowEventReceipt {
  event_id: number;
  step_index: number | null;
  event_type: string;
  status: string;
  recorded_at: string;
  approval: string | null;
  retry_count: number | null;
  attempt_phase: string | null;
}

export interface WorkflowRunDetail {
  graph_id: number;
  run_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  max_concurrency: number;
  retained: boolean;
  steps: WorkflowStepReceipt[];
  events: WorkflowEventReceipt[];
}

export interface WorkflowContents {
  input: unknown;
  output: unknown;
}

/** Align by live identity, not just by position. Pruned records have no
 * trustworthy remapped node identity: compare by operation and index only. */
export function alignWorkflowSteps(first: WorkflowRunDetail, second: WorkflowRunDetail) {
  const remaining = new Set(second.steps);
  const pairs: Array<{ first: WorkflowStepReceipt | null; second: WorkflowStepReceipt | null }> = [];
  for (const step of first.steps) {
    const match = [...remaining].find(candidate =>
      candidate.kind === step.kind && candidate.operation === step.operation &&
      (first.retained && second.retained
        ? !!step.nodeId && step.nodeId === candidate.nodeId
        : step.step_index === candidate.step_index));
    if (match) remaining.delete(match);
    pairs.push({ first: step, second: match ?? null });
  }
  for (const step of remaining) pairs.push({ first: null, second: step });
  return pairs;
}

export function changedWorkflowFields(first: WorkflowStepReceipt | null, second: WorkflowStepReceipt | null): string[] {
  if (!first || !second) return [first ? "Removed" : "Added"];
  const values = (s: WorkflowStepReceipt): Record<string, unknown> => ({
    Status: s.status, Input: s.input_ref.sha256, Configuration: s.input_ref.configuration,
    Output: s.output_ref?.sha256 ?? null, "Output revision": s.output_ref?.version ?? null,
    Provider: s.provider, Model: s.model, "Input tokens": s.input_tokens,
    "Output tokens": s.output_tokens, Cost: s.cost_usd, Approval: s.approval,
    Retries: s.retry_count, Duration: s.duration_ms, "Request phase": s.attempt_phase,
  });
  const left = values(first), right = values(second);
  return Object.keys(left).filter(key => left[key] !== right[key]);
}
