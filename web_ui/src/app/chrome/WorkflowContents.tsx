import { useId, useRef, useState } from "react";
import { Button } from "../ui/Button";
import { InlineAlert } from "../ui/InlineAlert";
import type { WorkflowContents } from "../canvas/workflowRunTypes";
import "./WorkflowHistoryDialog.css";

function bodyText(value: unknown): string {
  if (value === null || value === undefined) return "Unavailable: the recorded content is not retained or no longer matches.";
  return typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "Unavailable");
}

/** No content is requested or inserted into the DOM until explicit consent. */
export function FullWorkflowContents({ load, preview = false }: {
  load: () => Promise<WorkflowContents>; preview?: boolean;
}) {
  const contentId = useId();
  const loading = useRef(false);
  const [body, setBody] = useState<WorkflowContents | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle() {
    if (loading.current) return;
    if (body) { setBody(null); return; }
    loading.current = true;
    setBusy(true);
    setError("");
    try { setBody(await load()); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { loading.current = false; setBusy(false); }
  }
  return <div className="runner-fingerprints">
    <Button size="sm" variant="secondary" aria-disabled={busy || undefined} aria-busy={busy || undefined}
      aria-controls={contentId} aria-expanded={!!body} onClick={() => void toggle()}>
      {body ? "Hide full" : "Show full"}
    </Button>
    {error && <InlineAlert variant="error" title="Could not reveal contents" body={error} />}
    <div role="status">{busy ? "Loading full contents…" : ""}</div>
    <div id={contentId} hidden={!body}>
      {body && <>
        <div>Input</div><pre className="workflow-content" tabIndex={0} role="region" aria-label="Full input">{bodyText(body.input)}</pre>
        {!preview && <><div>Output</div><pre className="workflow-content" tabIndex={0} role="region" aria-label="Full output">{bodyText(body.output)}</pre></>}
      </>}
    </div>
  </div>;
}
