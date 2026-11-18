import { useState } from "react";
import type { ToolInvocationData } from "./ChatNodeView";
import "./ChatCitationChips.css";

/** A reserved, server-created provenance row, not an actual model tool call. */
export const KNOWLEDGE_CITATION_RECORD = "graphlink.knowledge.citations";
const KNOWLEDGE_RECORD_ID = "graphlink-knowledge-citations-v1";
const INLINE_CITATION = /\[(kb:[A-Za-z0-9_-]{1,128})\]/g;

export interface KnowledgeCitationSource {
  citationId: string;
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
  source: KnowledgeCitationSource | null;
}

export function citationsForReply(content: string, invocations: ToolInvocationData[]): DisplayCitation[] {
  // The model controls only the tokens in its answer. Source identities,
  // locations and excerpts must come from the server's structured record.
  const ids = Array.from(new Set(Array.from(content.matchAll(INLINE_CITATION), (match) => match[1])));
  const record = invocations.find((row) =>
    row.name === KNOWLEDGE_CITATION_RECORD && row.id === KNOWLEDGE_RECORD_ID);
  let sources: KnowledgeCitationSource[] = [];
  if (record) {
    try {
      const payload: unknown = JSON.parse(record.result);
      if (typeof payload === "object" && payload !== null &&
          "version" in payload && payload.version === 1 &&
          "sources" in payload && Array.isArray(payload.sources)) {
        sources = payload.sources.filter((source: unknown): source is KnowledgeCitationSource => {
          if (typeof source !== "object" || source === null) return false;
          const row = source as Partial<KnowledgeCitationSource>;
          return typeof row.citationId === "string" &&
            /^kb:[0-9a-f]{20}$/.test(row.citationId) &&
            typeof row.documentTitle === "string" &&
            typeof row.sourceUri === "string" &&
            typeof row.sourceFile === "string" &&
            typeof row.section === "string" &&
            typeof row.excerpt === "string" &&
            typeof row.offsetStart === "number" &&
            typeof row.offsetEnd === "number" &&
            row.cited === true;
        });
      }
    } catch {
      // A malformed or older metadata row never turns a model claim
      // into a verified link.
    }
  }
  const verified = new Map(sources.map((source) => [source.citationId, source]));
  return ids.map((citationId) => ({ citationId, source: verified.get(citationId) ?? null }));
}

/** Remote sources support heading fragments; local files open the exact saved excerpt. */
export function sectionUrl(source: KnowledgeCitationSource): string | null {
  try {
    const url = new URL(source.sourceUri);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (source.section && source.section !== "Unsectioned") {
      const slug = source.section.toLowerCase().trim()
        .replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s+/g, "-");
      if (slug) url.hash = slug;
    }
    return url.toString();
  } catch {
    return null;
  }
}

export function ChatCitationChips({ content, toolInvocations }: {
  content: string;
  toolInvocations: ToolInvocationData[];
}) {
  const citations = citationsForReply(content, toolInvocations);
  const [openCitationId, setOpenCitationId] = useState<string | null>(null);
  if (!citations.length) return null;

  const expanded = citations.find((citation) => citation.citationId === openCitationId)?.source;
  const originalUrl = expanded ? sectionUrl(expanded) : null;
  return (
    <section className="chat-citations" aria-label="Citations">
      <div className="chat-citations-heading">Sources cited</div>
      <div className="chat-citations-chips">
        {citations.map(({ citationId, source }) => (
          source ? (
            <button
              type="button"
              className="chat-citation-chip nodrag"
              key={citationId}
              aria-expanded={openCitationId === citationId}
              aria-label={"Open " + source.documentTitle + " at " + source.section}
              title={source.sourceFile + " · " + source.section}
              onClick={() => setOpenCitationId((current) => current === citationId ? null : citationId)}
            >
              {source.documentTitle} · {source.section}
            </button>
          ) : (
            <span key={citationId} role="status" className="chat-citation-unverified"
              title="Not in model context.">
              Unverified [{citationId}]
            </span>
          )
        ))}
      </div>
      {expanded && (
        <div className="chat-citation-excerpt" role="region" aria-label={"Source section: " + expanded.section}>
          <strong>{expanded.documentTitle} · {expanded.section}</strong>
          <div className="chat-citation-origin">{expanded.sourceFile} · Offset {expanded.offsetStart}–{expanded.offsetEnd}</div>
          <pre>{expanded.excerpt}</pre>
          {originalUrl && (
            <a href={originalUrl} target="_blank" rel="nofollow noopener noreferrer">
              Open source section
            </a>
          )}
        </div>
      )}
    </section>
  );
}
