/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_scene_payload.py::SceneStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields, hydrateRow, isRecord, wireDefaults } from "../wireCheck";

export type { ValidationResult };

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
  artifactError: string;
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
  codeReviewPrUrl: string;
  codeReviewRepo: string;
  codeReviewPrNumber: number;
  codeReviewPrTitle: string;
  codeReviewPrState: string;
  codeReviewPrHtmlUrl: string;
  codeReviewBaseRef: string;
  codeReviewHeadRef: string;
  codeReviewAdditions: number;
  codeReviewDeletions: number;
  codeReviewChangedFiles: number;
  codeReviewFiles: CodeReviewFileRow[];
  codeReviewFilesTruncated: boolean;
  codeReviewDiffTruncated: boolean;
  codeReviewDiffChars: number;
  codeReviewDiffVersion: number;
  codeReviewWalkthrough: CodeReviewWalkthroughGroupRow[];
  codeReviewFindings: CodeReviewFindingRow[];
  codeReviewErrors: CodeReviewErrorRow[];
  codeReviewDismissedIds: string[];
  codeReviewTitle: string;
  codeReviewOverview: string;
  codeReviewConfidence: string;
  codeReviewScores: Record<string, string>;
  codeReviewQualityScore: number;
  codeReviewVerdict: string;
  codeReviewRisk: string;
  codeReviewQualitySummary: string;
  codeReviewQa: CodeReviewQaRow[];
  codeReviewState: string;
  codeReviewError: string;
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
  harnessApprovalSessionOffered: boolean;
  harnessPlan: HarnessPlanStepRow[];
  harnessAwaitingQuestion: boolean;
  harnessQuestion: string;
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

export interface CodeReviewFileRow {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  patch: string;
  patchTruncated: boolean;
  previousPath?: string | null;
}

export interface CodeReviewWalkthroughGroupRow {
  groupTitle: string;
  paths: string[];
  explanation: string;
}

export interface CodeReviewFindingRow {
  id: string;
  severity: string;
  tier: string;
  category: string;
  path: string;
  line: number;
  title: string;
  evidence: string;
  impact: string;
  recommendation: string;
}

export interface CodeReviewErrorRow {
  id: string;
  severity: string;
  tier: string;
  kind: string;
  path: string;
  line: number;
  title: string;
  evidence: string;
  fix: string;
}

export interface CodeReviewQaRow {
  question: string;
  answer: string;
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

export interface HarnessPlanStepRow {
  text: string;
  status: string;
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

const CONVERSATION_MESSAGE_ROW_FIELDS: WireFields = [
  ["role", { e: ["user", "assistant"] }, 0],
  ["content", "s", 0],
  ["incomplete", "b", 0],
];

const RESEARCH_SOURCE_ROW_FIELDS: WireFields = [
  ["sourceId", "s", 0],
  ["title", "s", 0],
  ["url", "s", 0],
  ["canonicalUrl", "s", 0],
  ["snippet", "s", 0],
  ["rank", "n", 0],
  ["provider", "s", 0],
  ["finalUrl", "s", 0],
  ["status", "s", 0],
  ["errorCode", "s", 0],
  ["errorMessage", "s", 0],
  ["truncated", "b", 0],
  ["contentHash", "s", 0],
  ["citationCount", "n", 0],
];

const RESEARCH_CITATION_ROW_FIELDS: WireFields = [
  ["sourceId", "s", 0],
  ["marker", "s", 0],
  ["claimContext", "s", 0],
];

const RESEARCH_RESULT_ROW_FIELDS: WireFields = [
  ["requestId", "s", 0],
  ["originalQuery", "s", 0],
  ["effectiveQuery", "s", 0],
  ["answerMarkdown", "s", 0],
  ["sources", { a: { o: RESEARCH_SOURCE_ROW_FIELDS } }, 0],
  ["citations", { a: { o: RESEARCH_CITATION_ROW_FIELDS } }, 0],
  ["warnings", { a: "s" }, 0],
  ["providerSnapshot", { d: "s" }, 0],
];

const GITLINK_PENDING_CHANGE_ROW_FIELDS: WireFields = [
  ["path", "s", 0],
  ["operation", "s", 0],
  ["reason", "s", 0],
  ["content", "s", 1],
];

const CODE_REVIEW_FILE_ROW_FIELDS: WireFields = [
  ["path", "s", 0],
  ["status", "s", 0],
  ["additions", "n", 0],
  ["deletions", "n", 0],
  ["patch", "s", 0],
  ["patchTruncated", "b", 0],
  ["previousPath", "s", 1],
];

const CODE_REVIEW_WALKTHROUGH_GROUP_ROW_FIELDS: WireFields = [
  ["groupTitle", "s", 0],
  ["paths", { a: "s" }, 0],
  ["explanation", "s", 0],
];

const CODE_REVIEW_FINDING_ROW_FIELDS: WireFields = [
  ["id", "s", 0],
  ["severity", "s", 0],
  ["tier", "s", 0],
  ["category", "s", 0],
  ["path", "s", 0],
  ["line", "n", 0],
  ["title", "s", 0],
  ["evidence", "s", 0],
  ["impact", "s", 0],
  ["recommendation", "s", 0],
];

const CODE_REVIEW_ERROR_ROW_FIELDS: WireFields = [
  ["id", "s", 0],
  ["severity", "s", 0],
  ["tier", "s", 0],
  ["kind", "s", 0],
  ["path", "s", 0],
  ["line", "n", 0],
  ["title", "s", 0],
  ["evidence", "s", 0],
  ["fix", "s", 0],
];

const CODE_REVIEW_QA_ROW_FIELDS: WireFields = [
  ["question", "s", 0],
  ["answer", "s", 0],
];

const CHART_FLOW_ROW_FIELDS: WireFields = [
  ["source", "s", 0],
  ["target", "s", 0],
  ["value", "n", 0],
];

const CHART_DATA_ROW_FIELDS: WireFields = [
  ["version", "n", 1],
  ["type", { e: ["bar", "line", "pie", "histogram", "sankey"] }, 1],
  ["title", "s", 1],
  ["labels", { a: "s" }, 1],
  ["values", { a: "n" }, 1],
  ["xAxis", "s", 1],
  ["yAxis", "s", 1],
  ["bins", "n", 1],
  ["flows", { a: { o: CHART_FLOW_ROW_FIELDS } }, 1],
];

const TOOL_INVOCATION_ROW_FIELDS: WireFields = [
  ["id", "s", 0],
  ["name", "s", 0],
  ["argumentsJson", "s", 0],
  ["result", "s", 0],
  ["isError", "b", 0],
];

const PLAN_STEP_ROW_FIELDS: WireFields = [
  ["id", "s", 0],
  ["title", "s", 0],
  ["status", "s", 0],
  ["detail", "s", 0],
];

const BUILDER_ACTIVITY_ROW_FIELDS: WireFields = [
  ["tool", "s", 0],
  ["summary", "s", 0],
  ["outcome", "s", 0],
  ["stepId", "s", 0],
  ["elapsedMs", "n", 0],
];

const HARNESS_ACTIVITY_ROW_FIELDS: WireFields = [
  ["tool", "s", 0],
  ["summary", "s", 0],
  ["outcome", "s", 0],
  ["elapsedMs", "n", 0],
];

const HARNESS_PLAN_STEP_ROW_FIELDS: WireFields = [
  ["text", "s", 0],
  ["status", "s", 0],
];

const SCENE_NODE_ROW_FIELDS: WireFields = [
  ["id", "s", 0],
  ["x", "n", 0],
  ["y", "n", 0],
  ["title", "s", 0],
  ["kind", "s", 0],
  ["content", "s", 0, ""],
  ["isUser", "b", 0, false],
  ["isCollapsed", "b", 0, false],
  ["code", "s", 0, ""],
  ["language", "s", 0, ""],
  ["attachmentKind", "s", 0, ""],
  ["filePath", "s", 0, ""],
  ["mimeType", "s", 0, ""],
  ["durationSeconds", "n", 1, null],
  ["byteSize", "n", 1, null],
  ["previewLabel", "s", 0, ""],
  ["isDocked", "b", 0, false],
  ["imageAssetId", "s", 0, ""],
  ["history", { a: { o: CONVERSATION_MESSAGE_ROW_FIELDS } }, 0, []],
  ["pendingRequestId", "s", 1, null],
  ["researchStage", "s", 0, ""],
  ["researchCompleted", "n", 0, 0],
  ["researchTotal", "n", 0, 0],
  ["researchActiveSourceId", "s", 1, null],
  ["researchError", "s", 0, ""],
  ["researchResult", { o: RESEARCH_RESULT_ROW_FIELDS }, 1, null],
  ["researchRetainToKnowledge", "b", 0, false],
  ["artifactContent", "s", 0, ""],
  ["artifactError", "s", 0, ""],
  ["gitlinkRepo", "s", 0, ""],
  ["gitlinkBranch", "s", 0, ""],
  ["gitlinkScopeMode", "s", 0, "selected"],
  ["gitlinkLocalRoot", "s", 0, ""],
  ["gitlinkRepoFilePaths", { a: "s" }, 0, []],
  ["gitlinkSelectedPaths", { a: "s" }, 0, []],
  ["gitlinkTaskPrompt", "s", 0, ""],
  ["gitlinkContextStats", { d: "s" }, 0, {}],
  ["gitlinkContextSummary", "s", 0, ""],
  ["gitlinkContextVersion", "n", 0, 0],
  ["gitlinkProposalMarkdown", "s", 0, ""],
  ["gitlinkPendingChanges", { a: { o: GITLINK_PENDING_CHANGE_ROW_FIELDS } }, 0, []],
  ["gitlinkPreviewText", "s", 0, ""],
  ["gitlinkChangeFingerprint", "s", 1, null],
  ["gitlinkChangeState", "s", 0, "draft"],
  ["gitlinkError", "s", 0, ""],
  ["codeReviewPrUrl", "s", 0, ""],
  ["codeReviewRepo", "s", 0, ""],
  ["codeReviewPrNumber", "n", 0, 0],
  ["codeReviewPrTitle", "s", 0, ""],
  ["codeReviewPrState", "s", 0, ""],
  ["codeReviewPrHtmlUrl", "s", 0, ""],
  ["codeReviewBaseRef", "s", 0, ""],
  ["codeReviewHeadRef", "s", 0, ""],
  ["codeReviewAdditions", "n", 0, 0],
  ["codeReviewDeletions", "n", 0, 0],
  ["codeReviewChangedFiles", "n", 0, 0],
  ["codeReviewFiles", { a: { o: CODE_REVIEW_FILE_ROW_FIELDS } }, 0, []],
  ["codeReviewFilesTruncated", "b", 0, false],
  ["codeReviewDiffTruncated", "b", 0, false],
  ["codeReviewDiffChars", "n", 0, 0],
  ["codeReviewDiffVersion", "n", 0, 0],
  ["codeReviewWalkthrough", { a: { o: CODE_REVIEW_WALKTHROUGH_GROUP_ROW_FIELDS } }, 0, []],
  ["codeReviewFindings", { a: { o: CODE_REVIEW_FINDING_ROW_FIELDS } }, 0, []],
  ["codeReviewErrors", { a: { o: CODE_REVIEW_ERROR_ROW_FIELDS } }, 0, []],
  ["codeReviewDismissedIds", { a: "s" }, 0, []],
  ["codeReviewTitle", "s", 0, ""],
  ["codeReviewOverview", "s", 0, ""],
  ["codeReviewConfidence", "s", 0, ""],
  ["codeReviewScores", { d: "s" }, 0, {}],
  ["codeReviewQualityScore", "n", 0, 0],
  ["codeReviewVerdict", "s", 0, "none"],
  ["codeReviewRisk", "s", 0, ""],
  ["codeReviewQualitySummary", "s", 0, ""],
  ["codeReviewQa", { a: { o: CODE_REVIEW_QA_ROW_FIELDS } }, 0, []],
  ["codeReviewState", "s", 0, "draft"],
  ["codeReviewError", "s", 0, ""],
  ["codeSandboxRequirements", "s", 0, ""],
  ["codeSandboxPrompt", "s", 0, ""],
  ["codeSandboxCode", "s", 0, ""],
  ["codeSandboxOutput", "s", 0, ""],
  ["codeSandboxAnalysis", "s", 0, ""],
  ["codeSandboxAwaitingApproval", "b", 0, false],
  ["codeSandboxApprovalRequirements", "s", 0, ""],
  ["codeSandboxApprovalAllowSourceBuilds", "b", 0, false],
  ["codeSandboxApprovalIsRepair", "b", 0, false],
  ["codeSandboxError", "s", 0, ""],
  ["provider", "s", 1, null],
  ["model", "s", 1, null],
  ["isBranchSynthesis", "b", 0, false],
  ["synthesisInstructions", "s", 0, ""],
  ["branchStatus", "s", 0, "active"],
  ["responseIncomplete", "b", 0, false],
  ["promptTokens", "n", 1, null],
  ["completionTokens", "n", 1, null],
  ["estimatedCostUsd", "n", 1, null],
  ["isFinalDeliverable", "b", 0, false],
  ["color", "s", 1, null],
  ["headerColor", "s", 1, null],
  ["isSystemPrompt", "b", 0, false],
  ["isSummaryNote", "b", 0, false],
  ["isBranchComparison", "b", 0, false],
  ["itemIds", { a: "s" }, 0, []],
  ["isLocked", "b", 0, true],
  ["groupWidth", "n", 1, null],
  ["groupHeight", "n", 1, null],
  ["chartType", "s", 0, ""],
  ["chartData", { o: CHART_DATA_ROW_FIELDS }, 0, {}],
  ["chartError", "s", 0, ""],
  ["chartWidth", "n", 0, 480.0],
  ["chartHeight", "n", 0, 340.0],
  ["chartAspectLocked", "b", 0, true],
  ["chartSourceNodeId", "s", 0, ""],
  ["htmlSplitterState", "n", 1, null],
  ["chatScrollValue", "n", 0, 0.0],
  ["toolCalls", { a: { o: TOOL_INVOCATION_ROW_FIELDS } }, 0, []],
  ["overrideProvider", "s", 0, ""],
  ["overrideModelId", "s", 0, ""],
  ["indexIntoKnowledge", "b", 0, false],
  ["planGoal", "s", 0, ""],
  ["planSteps", { a: { o: PLAN_STEP_ROW_FIELDS } }, 0, []],
  ["builderActivity", { a: { o: BUILDER_ACTIVITY_ROW_FIELDS } }, 0, []],
  ["builderStatus", "s", 0, ""],
  ["builderMode", "s", 0, ""],
  ["builderRunId", "s", 0, ""],
  ["builderMaxSteps", "n", 0, 0],
  ["builderMaxTokens", "n", 0, 0],
  ["builderMaxWallSeconds", "n", 0, 0],
  ["builderSpentSteps", "n", 0, 0],
  ["builderSpentTokens", "n", 0, 0],
  ["builderSpentWallSeconds", "n", 0, 0],
  ["builderAwaitingToolApproval", "b", 0, false],
  ["builderApprovalToolName", "s", 0, ""],
  ["builderApprovalSummary", "s", 0, ""],
  ["builderStatusDetail", "s", 0, ""],
  ["harnessGoal", "s", 0, ""],
  ["harnessReply", "s", 0, ""],
  ["harnessStatus", "s", 0, ""],
  ["harnessStatusDetail", "s", 0, ""],
  ["harnessRunId", "s", 0, ""],
  ["harnessActivity", { a: { o: HARNESS_ACTIVITY_ROW_FIELDS } }, 0, []],
  ["harnessContextTokens", "n", 0, 0],
  ["harnessMaxContextTokens", "n", 0, 0],
  ["harnessCompactions", "n", 0, 0],
  ["harnessAwaitingApproval", "b", 0, false],
  ["harnessApprovalToolName", "s", 0, ""],
  ["harnessApprovalSummary", "s", 0, ""],
  ["harnessApprovalSessionOffered", "b", 0, false],
  ["harnessPlan", { a: { o: HARNESS_PLAN_STEP_ROW_FIELDS } }, 0, []],
  ["harnessAwaitingQuestion", "b", 0, false],
  ["harnessQuestion", "s", 0, ""],
  ["harnessWorkspacePath", "s", 0, ""],
  ["harnessWorkspaceActive", "s", 0, ""],
  ["harnessMaxTurns", "n", 0, 0],
  ["harnessSpentTurns", "n", 0, 0],
  ["harnessSpentTokens", "n", 0, 0],
  ["pluginState", { d: "s" }, 0, {}],
];

const SCENE_EDGE_ROW_FIELDS: WireFields = [
  ["id", "s", 0],
  ["source", "s", 0],
  ["target", "s", 0],
];

const SCENE_PIN_ROW_FIELDS: WireFields = [
  ["id", "s", 0],
  ["title", "s", 0],
  ["note", "s", 0],
  ["x", "n", 0],
  ["y", "n", 0],
];

const SCENE_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["nodes", { a: { o: SCENE_NODE_ROW_FIELDS } }, 0],
  ["edges", { a: { o: SCENE_EDGE_ROW_FIELDS } }, 0],
  ["pins", { a: { o: SCENE_PIN_ROW_FIELDS } }, 0],
  ["snapToGrid", "b", 0],
  ["fadeConnectionsEnabled", "b", 0],
  ["orthogonalRouting", "b", 0],
  ["smartGuides", "b", 0],
  ["hasSavedChat", "b", 0],
  ["dragFactor", "n", 0],
  ["fontFamily", "s", 0],
  ["fontSizePt", "n", 0],
  ["fontColor", "s", 0],
  ["canUndo", "b", 0],
  ["canRedo", "b", 0],
  ["undoLabel", "s", 0],
  ["redoLabel", "s", 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

/** SceneNodeRow crosses the wire sparse: the sender omits every field at its
 * declared default (contracts: WIRE_OMITS_DEFAULTS). These are those
 * defaults - the value every omitted field is restored to. */
export const SCENE_NODE_ROW_WIRE_DEFAULTS = wireDefaults(SCENE_NODE_ROW_FIELDS);

export function hydrateSceneNodeRow(value: unknown): unknown {
  return hydrateRow(SCENE_NODE_ROW_FIELDS, SCENE_NODE_ROW_WIRE_DEFAULTS, value);
}

function hydrateSceneState(value: unknown): unknown {
  if (!isRecord(value)) return value;
  let hydrated: Record<string, unknown> | null = null;
  {
    const rows = value["nodes"];
    if (Array.isArray(rows)) {
      const restored = rows.map(hydrateSceneNodeRow);
      if (restored.some((row, i) => row !== rows[i])) (hydrated ??= { ...value })["nodes"] = restored;
    }
  }
  return hydrated ?? value;
}

const checkSceneState = compileFields(SCENE_STATE_FIELDS);

export function validateSceneState(value: unknown): ValidationResult<SceneState> {
  const hydrated = hydrateSceneState(value);
  const errors: string[] = [];
  checkSceneState(hydrated, "$", errors);
  return errors.length === 0
    ? { ok: true, value: hydrated as SceneState }
    : { ok: false, errors };
}
