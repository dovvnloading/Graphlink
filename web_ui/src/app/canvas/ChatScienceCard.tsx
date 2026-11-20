import { formatMeasurement, formatScienceSummary, formatScienceValue, readableScienceKey, resultMeasurement, type ScienceResult } from "./scientificWorkbench";

// Mirrors the generated ScienceReferenceRow (web_ui/src/lib/bridge-core/
// generated/scene-state.ts) field-for-field, like ToolInvocationData.
export interface ScienceReferenceData {
  packId: string;
  packTitle: string;
  operationId: string;
  operationTitle: string;
  summary: string;
  headlineKey: string;
  headlineValueJson: string;
  headlineContextJson: string;
  resultHash: string;
  receiptHash: string;
}

function headlineValue(reference: ScienceReferenceData): string | null {
  if (!reference.headlineValueJson) return null;
  let nominal: string;
  try {
    nominal = formatScienceValue(JSON.parse(reference.headlineValueJson));
  } catch {
    return null;
  }
  if (!reference.headlineContextJson) return nominal;
  // Receipt-derived scalar siblings and propagated uncertainty are enough
  // to reuse the Workbench's formatting, without huge result JSON.
  try {
    const context = JSON.parse(reference.headlineContextJson) as Pick<ScienceResult, "values" | "uncertainty">;
    if (!context || !context.values || typeof context.values !== "object") return nominal;
    if (context.uncertainty?.unsupportedOutputs?.[reference.headlineKey])
      return `${nominal} · uncertainty unavailable`;
    const measured = resultMeasurement(context, reference.headlineKey);
    return measured && Number.isFinite(measured.value)
      && Number.isFinite(measured.standardUncertainty) && typeof measured.unit === "string"
      ? formatMeasurement(measured) : nominal;
  } catch {
    return nominal;
  }
}

/** A "Discuss in chat" card summarizing the verified source result.
 * The node's content is exactly what the model reads: full JSON for small
 * results, or explicitly marked excerpts for large ones. */
export function ChatScienceCard({ reference, content }: { reference: ScienceReferenceData; content: string }) {
  const value = reference.headlineKey ? headlineValue(reference) : null;
  const excerpted = content.includes("Result excerpts (inspect the source Workbench for complete data):");
  return (
    <section className="chat-science-card" aria-label={`Scientific result: ${reference.operationTitle}`}>
      <div className="chat-science-card-source">{reference.packTitle} · Scientific Workbench</div>
      <div className="chat-science-card-title">{reference.operationTitle}</div>
      {value !== null && (
        <div className="chat-science-card-highlight">
          <span title={reference.headlineKey}>{readableScienceKey(reference.headlineKey)}</span>
          <strong>{value}</strong>
        </div>
      )}
      <p className="chat-science-card-summary">{formatScienceSummary(reference.summary)}</p>
      <div className="chat-science-card-receipt"
        title={`Result hash ${reference.resultHash}\nReceipt hash ${reference.receiptHash}`}>
        Verified snapshot · receipt <code>{reference.receiptHash.slice(0, 8)}</code>
      </div>
      {excerpted && <p className="chat-science-card-excerpt" role="note">
        Only excerpts were sent to the model. Inspect or export the source Workbench result for every row, value and plot.
      </p>}
      <details className="chat-science-card-sent">
        <summary>Data sent to the model</summary>
        <pre className="nowheel nodrag">{content}</pre>
      </details>
    </section>
  );
}
