import type { WorkflowSpec, PortValue } from "../../lib/workflow-contract/generated/workflow";

export interface WorkflowParameter {
  name: string;
  step_id: string;
  setting: string;
  kind: "text" | "number" | "choice";
  choices: string[];
}
export interface WorkflowDocument {
  format: "graphlink-workflow";
  spec: WorkflowSpec;
  parameters: WorkflowParameter[];
}
export interface WorkflowVersion {
  revision: string;
  version: number;
  created_at: string;
}
export interface WorkflowEntry extends WorkflowVersion {
  workflow_id: string;
  name: string;
}
export interface WorkflowNodeState {
  document: WorkflowDocument;
  version: number;
  inputs: Record<string, unknown>;
  sources?: Record<string, string>;
  parameters: Record<string, string | number>;
  outputs: Record<string, PortValue>;
  lastInputHash: string;
  childRunId: string;
}
export interface WorkflowChildRun {
  runId: string;
  workflowId: string;
  revision: string;
  status: string;
  steps: { stepId: string; status: string; fingerprint: string | null }[];
}
export function workflowPin(doc: WorkflowDocument) {
  return { workflow_id: doc.spec.workflow_id, revision: doc.spec.revision };
}
export function readWorkflowNode(raw: string): WorkflowNodeState | null {
  try {
    const state = JSON.parse(raw) as WorkflowNodeState;
    return state.document?.format === "graphlink-workflow" && Array.isArray(state.document.spec?.steps)
      && Array.isArray(state.document.spec.inputs) && Array.isArray(state.document.spec.outputs)
      && Array.isArray(state.document.parameters) && state.inputs && state.outputs && state.parameters
      ? state : null;
  } catch { return null; }
}
