/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_scene_payload.py::SceneStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface SceneNodeRow {
  id: string;
  x: number;
  y: number;
  title: string;
  kind: string;
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  code: string;
  language: string;
  attachmentKind: string;
  filePath: string;
  mimeType: string;
  durationSeconds?: number | null;
  byteSize?: number | null;
  previewLabel: string;
  isDocked: boolean;
  imageAssetId: string;
  history: ConversationMessageRow[];
  pendingRequestId?: string | null;
  researchStage: string;
  researchCompleted: number;
  researchTotal: number;
  researchActiveSourceId?: string | null;
  researchError: string;
  researchResult?: ResearchResultRow | null;
  researchRetainToKnowledge: boolean;
  artifactContent: string;
  gitlinkRepo: string;
  gitlinkBranch: string;
  gitlinkScopeMode: string;
  gitlinkLocalRoot: string;
  gitlinkRepoFilePaths: string[];
  gitlinkSelectedPaths: string[];
  gitlinkTaskPrompt: string;
  gitlinkContextStats: Record<string, string>;
  gitlinkContextSummary: string;
  gitlinkContextVersion: number;
  gitlinkProposalMarkdown: string;
  gitlinkPendingChanges: GitlinkPendingChangeRow[];
  gitlinkPreviewText: string;
  gitlinkChangeFingerprint?: string | null;
  gitlinkChangeState: string;
  gitlinkError: string;
  codeSandboxRequirements: string;
  codeSandboxPrompt: string;
  codeSandboxCode: string;
  codeSandboxOutput: string;
  codeSandboxAnalysis: string;
  codeSandboxAwaitingApproval: boolean;
  codeSandboxApprovalRequirements: string;
  codeSandboxApprovalAllowSourceBuilds: boolean;
  codeSandboxApprovalIsRepair: boolean;
  codeSandboxError: string;
  provider?: string | null;
  model?: string | null;
  isBranchSynthesis: boolean;
  synthesisInstructions: string;
  branchStatus: string;
  responseIncomplete: boolean;
  promptTokens?: number | null;
  completionTokens?: number | null;
  estimatedCostUsd?: number | null;
  isFinalDeliverable: boolean;
  color?: string | null;
  headerColor?: string | null;
  isSystemPrompt: boolean;
  isSummaryNote: boolean;
  isBranchComparison: boolean;
  itemIds: string[];
  isLocked: boolean;
  groupWidth?: number | null;
  groupHeight?: number | null;
  chartType: string;
  chartData: ChartDataRow;
  chartError: string;
  chartWidth: number;
  chartHeight: number;
  chartAspectLocked: boolean;
  chartSourceNodeId: string;
  htmlSplitterState?: number | null;
  chatScrollValue: number;
  toolCalls: ToolInvocationRow[];
  overrideProvider: string;
  overrideModelId: string;
  indexIntoKnowledge: boolean;
  planGoal: string;
  planSteps: PlanStepRow[];
  builderActivity: BuilderActivityRow[];
  builderStatus: string;
  builderMode: string;
  builderRunId: string;
  builderMaxSteps: number;
  builderMaxTokens: number;
  builderMaxWallSeconds: number;
  builderSpentSteps: number;
  builderSpentTokens: number;
  builderSpentWallSeconds: number;
  builderAwaitingToolApproval: boolean;
  builderApprovalToolName: string;
  builderApprovalSummary: string;
  builderStatusDetail: string;
  harnessGoal: string;
  harnessReply: string;
  harnessStatus: string;
  harnessStatusDetail: string;
  harnessRunId: string;
  harnessActivity: HarnessActivityRow[];
  harnessContextTokens: number;
  harnessMaxContextTokens: number;
  harnessCompactions: number;
  harnessAwaitingApproval: boolean;
  harnessApprovalToolName: string;
  harnessApprovalSummary: string;
  harnessWorkspacePath: string;
  harnessWorkspaceActive: string;
  harnessMaxTurns: number;
  harnessSpentTurns: number;
  harnessSpentTokens: number;
  pluginState: Record<string, string>;
}

export interface ConversationMessageRow {
  role: "user" | "assistant";
  content: string;
  incomplete: boolean;
}

export interface ResearchResultRow {
  requestId: string;
  originalQuery: string;
  effectiveQuery: string;
  answerMarkdown: string;
  sources: ResearchSourceRow[];
  citations: ResearchCitationRow[];
  warnings: string[];
  providerSnapshot: Record<string, string>;
}

export interface ResearchSourceRow {
  sourceId: string;
  title: string;
  url: string;
  canonicalUrl: string;
  snippet: string;
  rank: number;
  provider: string;
  finalUrl: string;
  status: string;
  errorCode: string;
  errorMessage: string;
  truncated: boolean;
  contentHash: string;
  citationCount: number;
}

export interface ResearchCitationRow {
  sourceId: string;
  marker: string;
  claimContext: string;
}

export interface GitlinkPendingChangeRow {
  path: string;
  operation: string;
  reason: string;
  content?: string | null;
}

export interface ChartDataRow {
  version?: number | null;
  type?: "bar" | "line" | "pie" | "histogram" | "sankey" | null;
  title?: string | null;
  labels?: string[] | null;
  values?: number[] | null;
  xAxis?: string | null;
  yAxis?: string | null;
  bins?: number | null;
  flows?: ChartFlowRow[] | null;
}

export interface ChartFlowRow {
  source: string;
  target: string;
  value: number;
}

export interface ToolInvocationRow {
  id: string;
  name: string;
  argumentsJson: string;
  result: string;
  isError: boolean;
}

export interface PlanStepRow {
  id: string;
  title: string;
  status: string;
  detail: string;
}

export interface BuilderActivityRow {
  tool: string;
  summary: string;
  outcome: string;
  stepId: string;
  elapsedMs: number;
}

export interface HarnessActivityRow {
  tool: string;
  summary: string;
  outcome: string;
  elapsedMs: number;
}

export interface SceneEdgeRow {
  id: string;
  source: string;
  target: string;
}

export interface ScenePinRow {
  id: string;
  title: string;
  note: string;
  x: number;
  y: number;
}

export interface SceneState {
  schemaVersion: number;
  revision: number;
  nodes: SceneNodeRow[];
  edges: SceneEdgeRow[];
  pins: ScenePinRow[];
  snapToGrid: boolean;
  fadeConnectionsEnabled: boolean;
  orthogonalRouting: boolean;
  smartGuides: boolean;
  hasSavedChat: boolean;
  dragFactor: number;
  fontFamily: string;
  fontSizePt: number;
  fontColor: string;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string;
  redoLabel: string;
  minCompatibleSchemaVersion?: number | null;
}

export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errors: string[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Unknown keys are tolerated on purpose. The JSON Schema marks the contract
// additionalProperties:false because Python and the schema must not drift, but
// an incoming payload carrying a field this build has never heard of is the
// normal, expected shape of a NEWER compatible sender - rejecting it here would
// defeat the additive-forward-compatibility the version negotiation exists to
// provide. Missing or wrongly-typed KNOWN fields are still hard errors.

function checkSceneNodeRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["x"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.x: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.x` + ": expected number"); }
  }
  {
    const fieldValue = value["y"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.y: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.y` + ": expected number"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["kind"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.kind: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.kind` + ": expected string"); }
  }
  {
    const fieldValue = value["content"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.content: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.content` + ": expected string"); }
  }
  {
    const fieldValue = value["isUser"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isUser: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isUser` + ": expected boolean"); }
  }
  {
    const fieldValue = value["isCollapsed"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isCollapsed: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isCollapsed` + ": expected boolean"); }
  }
  {
    const fieldValue = value["code"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.code: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.code` + ": expected string"); }
  }
  {
    const fieldValue = value["language"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.language: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.language` + ": expected string"); }
  }
  {
    const fieldValue = value["attachmentKind"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.attachmentKind: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.attachmentKind` + ": expected string"); }
  }
  {
    const fieldValue = value["filePath"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.filePath: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.filePath` + ": expected string"); }
  }
  {
    const fieldValue = value["mimeType"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.mimeType: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.mimeType` + ": expected string"); }
  }
  {
    const fieldValue = value["durationSeconds"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.durationSeconds` + ": expected number"); }
  }
  {
    const fieldValue = value["byteSize"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.byteSize` + ": expected number"); }
  }
  {
    const fieldValue = value["previewLabel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.previewLabel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.previewLabel` + ": expected string"); }
  }
  {
    const fieldValue = value["isDocked"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isDocked: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isDocked` + ": expected boolean"); }
  }
  {
    const fieldValue = value["imageAssetId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.imageAssetId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.imageAssetId` + ": expected string"); }
  }
  {
    const fieldValue = value["history"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.history: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.history` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkConversationMessageRow(item, `${path}.history` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["pendingRequestId"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.pendingRequestId` + ": expected string"); }
  }
  {
    const fieldValue = value["researchStage"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.researchStage: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.researchStage` + ": expected string"); }
  }
  {
    const fieldValue = value["researchCompleted"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.researchCompleted: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.researchCompleted` + ": expected number"); }
  }
  {
    const fieldValue = value["researchTotal"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.researchTotal: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.researchTotal` + ": expected number"); }
  }
  {
    const fieldValue = value["researchActiveSourceId"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.researchActiveSourceId` + ": expected string"); }
  }
  {
    const fieldValue = value["researchError"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.researchError: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.researchError` + ": expected string"); }
  }
  {
    const fieldValue = value["researchResult"];
    if (fieldValue !== undefined && fieldValue !== null) { checkResearchResultRow(fieldValue, `${path}.researchResult`, errors); }
  }
  {
    const fieldValue = value["researchRetainToKnowledge"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.researchRetainToKnowledge: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.researchRetainToKnowledge` + ": expected boolean"); }
  }
  {
    const fieldValue = value["artifactContent"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.artifactContent: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.artifactContent` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkRepo"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkRepo: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkRepo` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkBranch"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkBranch: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkBranch` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkScopeMode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkScopeMode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkScopeMode` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkLocalRoot"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkLocalRoot: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkLocalRoot` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkRepoFilePaths"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkRepoFilePaths: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.gitlinkRepoFilePaths` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.gitlinkRepoFilePaths` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["gitlinkSelectedPaths"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkSelectedPaths: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.gitlinkSelectedPaths` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.gitlinkSelectedPaths` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["gitlinkTaskPrompt"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkTaskPrompt: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkTaskPrompt` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkContextStats"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkContextStats: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.gitlinkContextStats` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "string") errors.push(`${path}.gitlinkContextStats` + `[${JSON.stringify(k)}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["gitlinkContextSummary"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkContextSummary: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkContextSummary` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkContextVersion"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkContextVersion: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.gitlinkContextVersion` + ": expected number"); }
  }
  {
    const fieldValue = value["gitlinkProposalMarkdown"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkProposalMarkdown: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkProposalMarkdown` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkPendingChanges"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkPendingChanges: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.gitlinkPendingChanges` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkGitlinkPendingChangeRow(item, `${path}.gitlinkPendingChanges` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["gitlinkPreviewText"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkPreviewText: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkPreviewText` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkChangeFingerprint"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkChangeFingerprint` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkChangeState"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkChangeState: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkChangeState` + ": expected string"); }
  }
  {
    const fieldValue = value["gitlinkError"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gitlinkError: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gitlinkError` + ": expected string"); }
  }
  {
    const fieldValue = value["codeSandboxRequirements"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxRequirements: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.codeSandboxRequirements` + ": expected string"); }
  }
  {
    const fieldValue = value["codeSandboxPrompt"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxPrompt: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.codeSandboxPrompt` + ": expected string"); }
  }
  {
    const fieldValue = value["codeSandboxCode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxCode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.codeSandboxCode` + ": expected string"); }
  }
  {
    const fieldValue = value["codeSandboxOutput"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxOutput: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.codeSandboxOutput` + ": expected string"); }
  }
  {
    const fieldValue = value["codeSandboxAnalysis"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxAnalysis: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.codeSandboxAnalysis` + ": expected string"); }
  }
  {
    const fieldValue = value["codeSandboxAwaitingApproval"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxAwaitingApproval: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.codeSandboxAwaitingApproval` + ": expected boolean"); }
  }
  {
    const fieldValue = value["codeSandboxApprovalRequirements"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxApprovalRequirements: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.codeSandboxApprovalRequirements` + ": expected string"); }
  }
  {
    const fieldValue = value["codeSandboxApprovalAllowSourceBuilds"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxApprovalAllowSourceBuilds: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.codeSandboxApprovalAllowSourceBuilds` + ": expected boolean"); }
  }
  {
    const fieldValue = value["codeSandboxApprovalIsRepair"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxApprovalIsRepair: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.codeSandboxApprovalIsRepair` + ": expected boolean"); }
  }
  {
    const fieldValue = value["codeSandboxError"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.codeSandboxError: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.codeSandboxError` + ": expected string"); }
  }
  {
    const fieldValue = value["provider"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.provider` + ": expected string"); }
  }
  {
    const fieldValue = value["model"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.model` + ": expected string"); }
  }
  {
    const fieldValue = value["isBranchSynthesis"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isBranchSynthesis: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isBranchSynthesis` + ": expected boolean"); }
  }
  {
    const fieldValue = value["synthesisInstructions"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.synthesisInstructions: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.synthesisInstructions` + ": expected string"); }
  }
  {
    const fieldValue = value["branchStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.branchStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.branchStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["responseIncomplete"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.responseIncomplete: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.responseIncomplete` + ": expected boolean"); }
  }
  {
    const fieldValue = value["promptTokens"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.promptTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["completionTokens"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.completionTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["estimatedCostUsd"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.estimatedCostUsd` + ": expected number"); }
  }
  {
    const fieldValue = value["isFinalDeliverable"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isFinalDeliverable: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isFinalDeliverable` + ": expected boolean"); }
  }
  {
    const fieldValue = value["color"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.color` + ": expected string"); }
  }
  {
    const fieldValue = value["headerColor"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.headerColor` + ": expected string"); }
  }
  {
    const fieldValue = value["isSystemPrompt"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isSystemPrompt: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isSystemPrompt` + ": expected boolean"); }
  }
  {
    const fieldValue = value["isSummaryNote"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isSummaryNote: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isSummaryNote` + ": expected boolean"); }
  }
  {
    const fieldValue = value["isBranchComparison"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isBranchComparison: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isBranchComparison` + ": expected boolean"); }
  }
  {
    const fieldValue = value["itemIds"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.itemIds: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.itemIds` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.itemIds` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["isLocked"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isLocked: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isLocked` + ": expected boolean"); }
  }
  {
    const fieldValue = value["groupWidth"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.groupWidth` + ": expected number"); }
  }
  {
    const fieldValue = value["groupHeight"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.groupHeight` + ": expected number"); }
  }
  {
    const fieldValue = value["chartType"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chartType: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.chartType` + ": expected string"); }
  }
  {
    const fieldValue = value["chartData"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chartData: missing required field`);
    else { checkChartDataRow(fieldValue, `${path}.chartData`, errors); }
  }
  {
    const fieldValue = value["chartError"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chartError: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.chartError` + ": expected string"); }
  }
  {
    const fieldValue = value["chartWidth"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chartWidth: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.chartWidth` + ": expected number"); }
  }
  {
    const fieldValue = value["chartHeight"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chartHeight: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.chartHeight` + ": expected number"); }
  }
  {
    const fieldValue = value["chartAspectLocked"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chartAspectLocked: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.chartAspectLocked` + ": expected boolean"); }
  }
  {
    const fieldValue = value["chartSourceNodeId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chartSourceNodeId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.chartSourceNodeId` + ": expected string"); }
  }
  {
    const fieldValue = value["htmlSplitterState"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.htmlSplitterState` + ": expected number"); }
  }
  {
    const fieldValue = value["chatScrollValue"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.chatScrollValue: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.chatScrollValue` + ": expected number"); }
  }
  {
    const fieldValue = value["toolCalls"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.toolCalls: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.toolCalls` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkToolInvocationRow(item, `${path}.toolCalls` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["overrideProvider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.overrideProvider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.overrideProvider` + ": expected string"); }
  }
  {
    const fieldValue = value["overrideModelId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.overrideModelId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.overrideModelId` + ": expected string"); }
  }
  {
    const fieldValue = value["indexIntoKnowledge"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.indexIntoKnowledge: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.indexIntoKnowledge` + ": expected boolean"); }
  }
  {
    const fieldValue = value["planGoal"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.planGoal: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.planGoal` + ": expected string"); }
  }
  {
    const fieldValue = value["planSteps"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.planSteps: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.planSteps` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkPlanStepRow(item, `${path}.planSteps` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["builderActivity"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderActivity: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.builderActivity` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkBuilderActivityRow(item, `${path}.builderActivity` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["builderStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.builderStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["builderMode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderMode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.builderMode` + ": expected string"); }
  }
  {
    const fieldValue = value["builderRunId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderRunId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.builderRunId` + ": expected string"); }
  }
  {
    const fieldValue = value["builderMaxSteps"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderMaxSteps: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.builderMaxSteps` + ": expected number"); }
  }
  {
    const fieldValue = value["builderMaxTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderMaxTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.builderMaxTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["builderMaxWallSeconds"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderMaxWallSeconds: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.builderMaxWallSeconds` + ": expected number"); }
  }
  {
    const fieldValue = value["builderSpentSteps"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderSpentSteps: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.builderSpentSteps` + ": expected number"); }
  }
  {
    const fieldValue = value["builderSpentTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderSpentTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.builderSpentTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["builderSpentWallSeconds"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderSpentWallSeconds: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.builderSpentWallSeconds` + ": expected number"); }
  }
  {
    const fieldValue = value["builderAwaitingToolApproval"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderAwaitingToolApproval: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.builderAwaitingToolApproval` + ": expected boolean"); }
  }
  {
    const fieldValue = value["builderApprovalToolName"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderApprovalToolName: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.builderApprovalToolName` + ": expected string"); }
  }
  {
    const fieldValue = value["builderApprovalSummary"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderApprovalSummary: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.builderApprovalSummary` + ": expected string"); }
  }
  {
    const fieldValue = value["builderStatusDetail"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.builderStatusDetail: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.builderStatusDetail` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessGoal"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessGoal: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessGoal` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessReply"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessReply: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessReply` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessStatusDetail"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessStatusDetail: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessStatusDetail` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessRunId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessRunId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessRunId` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessActivity"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessActivity: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.harnessActivity` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkHarnessActivityRow(item, `${path}.harnessActivity` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["harnessContextTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessContextTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.harnessContextTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["harnessMaxContextTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessMaxContextTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.harnessMaxContextTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["harnessCompactions"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessCompactions: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.harnessCompactions` + ": expected number"); }
  }
  {
    const fieldValue = value["harnessAwaitingApproval"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessAwaitingApproval: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.harnessAwaitingApproval` + ": expected boolean"); }
  }
  {
    const fieldValue = value["harnessApprovalToolName"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessApprovalToolName: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessApprovalToolName` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessApprovalSummary"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessApprovalSummary: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessApprovalSummary` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessWorkspacePath"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessWorkspacePath: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessWorkspacePath` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessWorkspaceActive"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessWorkspaceActive: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.harnessWorkspaceActive` + ": expected string"); }
  }
  {
    const fieldValue = value["harnessMaxTurns"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessMaxTurns: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.harnessMaxTurns` + ": expected number"); }
  }
  {
    const fieldValue = value["harnessSpentTurns"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessSpentTurns: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.harnessSpentTurns` + ": expected number"); }
  }
  {
    const fieldValue = value["harnessSpentTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.harnessSpentTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.harnessSpentTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["pluginState"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.pluginState: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.pluginState` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "string") errors.push(`${path}.pluginState` + `[${JSON.stringify(k)}]` + ": expected string"); }); }
  }
}

function checkConversationMessageRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["role"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.role: missing required field`);
    else { if (!["user", "assistant"].includes(fieldValue as string)) errors.push(`${path}.role` + `: ${JSON.stringify(fieldValue)} is not one of [` + "user, assistant" + `]`); }
  }
  {
    const fieldValue = value["content"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.content: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.content` + ": expected string"); }
  }
  {
    const fieldValue = value["incomplete"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.incomplete: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.incomplete` + ": expected boolean"); }
  }
}

function checkResearchResultRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["requestId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.requestId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.requestId` + ": expected string"); }
  }
  {
    const fieldValue = value["originalQuery"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.originalQuery: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.originalQuery` + ": expected string"); }
  }
  {
    const fieldValue = value["effectiveQuery"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.effectiveQuery: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.effectiveQuery` + ": expected string"); }
  }
  {
    const fieldValue = value["answerMarkdown"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.answerMarkdown: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.answerMarkdown` + ": expected string"); }
  }
  {
    const fieldValue = value["sources"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sources: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.sources` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkResearchSourceRow(item, `${path}.sources` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["citations"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.citations: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.citations` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkResearchCitationRow(item, `${path}.citations` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["warnings"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.warnings: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.warnings` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.warnings` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["providerSnapshot"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.providerSnapshot: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.providerSnapshot` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "string") errors.push(`${path}.providerSnapshot` + `[${JSON.stringify(k)}]` + ": expected string"); }); }
  }
}

function checkResearchSourceRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["sourceId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sourceId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.sourceId` + ": expected string"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["url"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.url: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.url` + ": expected string"); }
  }
  {
    const fieldValue = value["canonicalUrl"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.canonicalUrl: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.canonicalUrl` + ": expected string"); }
  }
  {
    const fieldValue = value["snippet"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.snippet: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.snippet` + ": expected string"); }
  }
  {
    const fieldValue = value["rank"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.rank: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.rank` + ": expected number"); }
  }
  {
    const fieldValue = value["provider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.provider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.provider` + ": expected string"); }
  }
  {
    const fieldValue = value["finalUrl"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.finalUrl: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.finalUrl` + ": expected string"); }
  }
  {
    const fieldValue = value["status"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.status: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.status` + ": expected string"); }
  }
  {
    const fieldValue = value["errorCode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.errorCode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.errorCode` + ": expected string"); }
  }
  {
    const fieldValue = value["errorMessage"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.errorMessage: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.errorMessage` + ": expected string"); }
  }
  {
    const fieldValue = value["truncated"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.truncated: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.truncated` + ": expected boolean"); }
  }
  {
    const fieldValue = value["contentHash"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.contentHash: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.contentHash` + ": expected string"); }
  }
  {
    const fieldValue = value["citationCount"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.citationCount: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.citationCount` + ": expected number"); }
  }
}

function checkResearchCitationRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["sourceId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sourceId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.sourceId` + ": expected string"); }
  }
  {
    const fieldValue = value["marker"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.marker: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.marker` + ": expected string"); }
  }
  {
    const fieldValue = value["claimContext"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.claimContext: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.claimContext` + ": expected string"); }
  }
}

function checkGitlinkPendingChangeRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["path"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.path: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.path` + ": expected string"); }
  }
  {
    const fieldValue = value["operation"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.operation: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.operation` + ": expected string"); }
  }
  {
    const fieldValue = value["reason"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.reason: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.reason` + ": expected string"); }
  }
  {
    const fieldValue = value["content"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.content` + ": expected string"); }
  }
}

function checkChartDataRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["version"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.version` + ": expected number"); }
  }
  {
    const fieldValue = value["type"];
    if (fieldValue !== undefined && fieldValue !== null) { if (!["bar", "line", "pie", "histogram", "sankey"].includes(fieldValue as string)) errors.push(`${path}.type` + `: ${JSON.stringify(fieldValue)} is not one of [` + "bar, line, pie, histogram, sankey" + `]`); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["labels"];
    if (fieldValue !== undefined && fieldValue !== null) { if (!Array.isArray(fieldValue)) errors.push(`${path}.labels` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.labels` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["values"];
    if (fieldValue !== undefined && fieldValue !== null) { if (!Array.isArray(fieldValue)) errors.push(`${path}.values` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "number") errors.push(`${path}.values` + `[${i}]` + ": expected number"); }); }
  }
  {
    const fieldValue = value["xAxis"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.xAxis` + ": expected string"); }
  }
  {
    const fieldValue = value["yAxis"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.yAxis` + ": expected string"); }
  }
  {
    const fieldValue = value["bins"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.bins` + ": expected number"); }
  }
  {
    const fieldValue = value["flows"];
    if (fieldValue !== undefined && fieldValue !== null) { if (!Array.isArray(fieldValue)) errors.push(`${path}.flows` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkChartFlowRow(item, `${path}.flows` + `[${i}]`, errors); }); }
  }
}

function checkChartFlowRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["source"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.source: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.source` + ": expected string"); }
  }
  {
    const fieldValue = value["target"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.target: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.target` + ": expected string"); }
  }
  {
    const fieldValue = value["value"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.value: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.value` + ": expected number"); }
  }
}

function checkToolInvocationRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["name"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.name: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.name` + ": expected string"); }
  }
  {
    const fieldValue = value["argumentsJson"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.argumentsJson: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.argumentsJson` + ": expected string"); }
  }
  {
    const fieldValue = value["result"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.result: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.result` + ": expected string"); }
  }
  {
    const fieldValue = value["isError"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.isError: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.isError` + ": expected boolean"); }
  }
}

function checkPlanStepRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["status"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.status: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.status` + ": expected string"); }
  }
  {
    const fieldValue = value["detail"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.detail: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.detail` + ": expected string"); }
  }
}

function checkBuilderActivityRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["tool"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.tool: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.tool` + ": expected string"); }
  }
  {
    const fieldValue = value["summary"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.summary: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.summary` + ": expected string"); }
  }
  {
    const fieldValue = value["outcome"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.outcome: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.outcome` + ": expected string"); }
  }
  {
    const fieldValue = value["stepId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.stepId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.stepId` + ": expected string"); }
  }
  {
    const fieldValue = value["elapsedMs"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.elapsedMs: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.elapsedMs` + ": expected number"); }
  }
}

function checkHarnessActivityRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["tool"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.tool: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.tool` + ": expected string"); }
  }
  {
    const fieldValue = value["summary"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.summary: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.summary` + ": expected string"); }
  }
  {
    const fieldValue = value["outcome"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.outcome: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.outcome` + ": expected string"); }
  }
  {
    const fieldValue = value["elapsedMs"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.elapsedMs: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.elapsedMs` + ": expected number"); }
  }
}

function checkSceneEdgeRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["source"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.source: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.source` + ": expected string"); }
  }
  {
    const fieldValue = value["target"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.target: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.target` + ": expected string"); }
  }
}

function checkScenePinRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["note"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.note: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.note` + ": expected string"); }
  }
  {
    const fieldValue = value["x"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.x: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.x` + ": expected number"); }
  }
  {
    const fieldValue = value["y"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.y: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.y` + ": expected number"); }
  }
}

function checkSceneState(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["schemaVersion"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.schemaVersion: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.schemaVersion` + ": expected number"); }
  }
  {
    const fieldValue = value["revision"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.revision: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.revision` + ": expected number"); }
  }
  {
    const fieldValue = value["nodes"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.nodes: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.nodes` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkSceneNodeRow(item, `${path}.nodes` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["edges"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.edges: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.edges` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkSceneEdgeRow(item, `${path}.edges` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["pins"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.pins: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.pins` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkScenePinRow(item, `${path}.pins` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["snapToGrid"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.snapToGrid: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.snapToGrid` + ": expected boolean"); }
  }
  {
    const fieldValue = value["fadeConnectionsEnabled"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fadeConnectionsEnabled: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.fadeConnectionsEnabled` + ": expected boolean"); }
  }
  {
    const fieldValue = value["orthogonalRouting"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.orthogonalRouting: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.orthogonalRouting` + ": expected boolean"); }
  }
  {
    const fieldValue = value["smartGuides"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.smartGuides: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.smartGuides` + ": expected boolean"); }
  }
  {
    const fieldValue = value["hasSavedChat"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.hasSavedChat: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.hasSavedChat` + ": expected boolean"); }
  }
  {
    const fieldValue = value["dragFactor"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.dragFactor: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.dragFactor` + ": expected number"); }
  }
  {
    const fieldValue = value["fontFamily"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fontFamily: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.fontFamily` + ": expected string"); }
  }
  {
    const fieldValue = value["fontSizePt"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fontSizePt: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.fontSizePt` + ": expected number"); }
  }
  {
    const fieldValue = value["fontColor"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fontColor: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.fontColor` + ": expected string"); }
  }
  {
    const fieldValue = value["canUndo"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.canUndo: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.canUndo` + ": expected boolean"); }
  }
  {
    const fieldValue = value["canRedo"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.canRedo: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.canRedo` + ": expected boolean"); }
  }
  {
    const fieldValue = value["undoLabel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.undoLabel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.undoLabel` + ": expected string"); }
  }
  {
    const fieldValue = value["redoLabel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.redoLabel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.redoLabel` + ": expected string"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateSceneState(value: unknown): ValidationResult<SceneState> {
  const errors: string[] = [];
  checkSceneState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as SceneState }
    : { ok: false, errors };
}
