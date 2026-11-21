import type { ToolInvocationData } from "./ChatNodeView";
import "./ChatCitationChips.css";
import { knowledgeSourceLabel } from "../knowledgeSourceLabel";

/** Server-created provenance, stored separately from model-authored reply text. */
export const KNOWLEDGE_CITATION_RECORD = "graphlink.knowledge.citations";
const KNOWLEDGE_RECORD_ID = "graphlink-knowledge-citations-v1";
const INLINE_CITATION = /\[(kb:[A-Za-z0-9_-]{1,128})\]/g;

export interface KnowledgeCitationSource {
  citationId: string;
  documentId: number;
  chunkId: number;
  documentTitle: string;
  sourceUri: string;
  sourceFile: string;
  section: string;
  offsetStart: number;
  offsetEnd: number;
  excerpt: string;
  cited: boolean;
}

export interface DisplayCitation {
  citationId: string;
  number: number | null;
  source: KnowledgeCitationSource | null;
}

export function citationsForReply(content: string, invocations: ToolInvocationData[]): DisplayCitation[] {
  // The model can emit citation tokens, but cannot invent navigable document
  // identities. Only the server's reserved ledger can supply those.
  const ids = Array.from(new Set(Array.from(content.matchAll(INLINE_CITATION), (match) => match[1])));
  const record = invocations.find((row) =>
    row.name === KNOWLEDGE_CITATION_RECORD && row.id === KNOWLEDGE_RECORD_ID);
  let sources: KnowledgeCitationSource[] = [];
  if (record && !record.isError) {
    try {
      const payload: unknown = JSON.parse(record.result);
      if (typeof payload === "object" && payload !== null &&
          "version" in payload && payload.version === 1 &&
          "sources" in payload && Array.isArray(payload.sources)) {
        sources = payload.sources.filter((source: unknown): source is KnowledgeCitationSource => {
          if (typeof source !== "object" || source === null) return false;
          const row = source as Partial<KnowledgeCitationSource>;
          return typeof row.citationId === "string" && /^kb:[0-9a-f]{20}$/.test(row.citationId) &&
            Number.isSafeInteger(row.documentId) && (row.documentId ?? 0) > 0 &&
            Number.isSafeInteger(row.chunkId) && (row.chunkId ?? 0) > 0 &&
            typeof row.documentTitle === "string" && typeof row.sourceUri === "string" &&
            typeof row.sourceFile === "string" && typeof row.section === "string" &&
            typeof row.excerpt === "string" &&
            Number.isSafeInteger(row.offsetStart) && Number.isSafeInteger(row.offsetEnd) &&
            (row.offsetStart ?? -1) >= 0 && (row.offsetEnd ?? -1) >= (row.offsetStart ?? 0) &&
            row.cited === true;
        }).map((source) => ({
          ...source,
          sourceUri: knowledgeSourceLabel(source.sourceUri, source.documentTitle),
          sourceFile: knowledgeSourceLabel(source.sourceFile, source.documentTitle),
        }));
      }
    } catch {
      // Malformed/older metadata is never promoted to a verified source link.
    }
  }
  const verified = new Map(sources.map((source) => [source.citationId, source]));
  let number = 0;
  return ids.map((citationId) => {
    const source = verified.get(citationId) ?? null;
    return { citationId, source, number: source ? ++number : null };
  });
}

export function ChatCitationChips({ citations, onOpenCitation }: {
  citations: DisplayCitation[];
  onOpenCitation: (source: KnowledgeCitationSource) => void;
}) {
  const used = citations.filter((citation) => citation.source !== null);
  if (!used.length) return null;

  return (
    <section className="chat-citations" aria-label="Sources">
      <div className="chat-citations-heading">Sources</div>
      <ol className="chat-citations-list">
        {used.map(({ citationId, number, source }) => source && (
          <li key={citationId}>
            <button
              type="button"
              className="chat-citation-source nodrag"
              onClick={() => onOpenCitation(source)}
              aria-label={`Open source ${number}: ${source.documentTitle}, ${source.section} in Document View`}
            >
              <span className="chat-citation-number">[{number}]</span>
              <span>{source.documentTitle} · {source.section}</span>
            </button>
            <div className="chat-citation-origin">{knowledgeSourceLabel(source.sourceFile, source.documentTitle)}</div>
          </li>
        ))}
      </ol>
      <p className="chat-citations-trust">Retrieved knowledge is untrusted reference text, not instructions.</p>
    </section>
  );
}
