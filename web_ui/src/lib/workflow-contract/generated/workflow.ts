// GENERATED - do not hand-edit. Source: backend/workflow/contracts.py and ports.py.

export type PortKind = "text" | "json" | "table" | "file" | "image";

export type SideEffectClass = "pure" | "local-read" | "cloud" | "executes" | "writes";

export type JsonScalar = string | number | boolean | null;

export type JsonData = JsonScalar | ReadonlyArray<JsonData> | { readonly [key: string]: JsonData };

export interface PortSpec {
  readonly name: string;
  readonly kind: PortKind;
  readonly required: boolean;
}

export interface PortSet {
  readonly inputs: ReadonlyArray<PortSpec>;
  readonly outputs: ReadonlyArray<PortSpec>;
}

export interface AssetRef {
  readonly asset_id: string;
  readonly sha256: string;
  readonly media_type: string;
  readonly filename: string;
  readonly size_bytes: number;
}

export interface TextValue {
  readonly kind: "text";
  readonly text: string;
}

export interface JsonValue {
  readonly kind: "json";
  readonly value: JsonData;
}

export interface TableValue {
  readonly kind: "table";
  readonly columns: ReadonlyArray<string>;
  readonly rows: ReadonlyArray<ReadonlyArray<JsonScalar>>;
}

export interface FileValue {
  readonly kind: "file";
  readonly asset: AssetRef;
}

export interface ImageValue {
  readonly kind: "image";
  readonly asset: AssetRef;
}

export type PortValue = TextValue | JsonValue | TableValue | FileValue | ImageValue;

export interface AdapterDescriptor {
  readonly adapter_id: string;
  readonly adapter_version: string;
  readonly ports: PortSet;
  readonly side_effect_class: SideEffectClass;
}

export interface WorkflowInputSource {
  readonly kind: "input";
  readonly port: string;
}

export interface StepOutputSource {
  readonly kind: "step";
  readonly step_id: string;
  readonly port: string;
}

export type InputSource = WorkflowInputSource | StepOutputSource;

export interface InputBinding {
  readonly port: string;
  readonly source: InputSource;
}

export interface WorkflowOutput {
  readonly port: PortSpec;
  readonly source: InputSource;
}

export interface WorkflowStepSpec {
  readonly step_id: string;
  readonly adapter: AdapterDescriptor;
  readonly config_json: string;
  readonly bindings: ReadonlyArray<InputBinding>;
}

export interface WorkflowSpec {
  readonly schema_version: 1;
  readonly workflow_id: string;
  readonly revision: string;
  readonly name: string;
  readonly inputs: ReadonlyArray<PortSpec>;
  readonly steps: ReadonlyArray<WorkflowStepSpec>;
  readonly outputs: ReadonlyArray<WorkflowOutput>;
}

export type RunStatus = "pending" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

export type StepStatus = "pending" | "running" | "completed" | "failed" | "cancelled" | "blocked" | "cached" | "skipped" | "interrupted";

export type EventKind = "run-created" | "run-started" | "run-completed" | "run-failed" | "run-cancelled" | "run-interrupted" | "step-started" | "step-progress" | "step-completed" | "step-failed" | "step-cancelled" | "step-blocked" | "step-cached" | "step-skipped" | "step-interrupted";

export interface WorkflowRunRecord {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly spec: WorkflowSpec;
  readonly spec_fingerprint: string;
  readonly status: RunStatus;
  readonly created_at: string;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly inputs: { readonly [key: string]: PortValue };
  readonly outputs: { readonly [key: string]: PortValue };
  readonly error: string | null;
}

export interface WorkflowStepRecord {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly step_id: string;
  readonly attempt: number;
  readonly side_effect_class: SideEffectClass;
  readonly status: StepStatus;
  readonly fingerprint: string | null;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly outputs: { readonly [key: string]: PortValue };
  readonly error: string | null;
}

export interface WorkflowEventRecord {
  readonly schema_version: 1;
  readonly run_id: string;
  readonly sequence: number;
  readonly timestamp: string;
  readonly kind: EventKind;
  readonly step_id: string | null;
  readonly attempt: number | null;
  readonly message: string;
  readonly data: JsonData;
}

export const ALLOWED_COERCIONS = [["json", "text"], ["table", "text"], ["table", "json"]] as const;
