/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_scene_payload.py::SceneStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
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
}

export interface ConversationMessageRow {
  role: "user" | "assistant";
  content: string;
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
  dragFactor: number;
  fontFamily: string;
  fontSizePt: number;
  fontColor: string;
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
