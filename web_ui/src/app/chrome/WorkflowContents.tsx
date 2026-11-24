import { useState } from "react";
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
  const [body, setBody] = useState<WorkflowContents | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle() {
    if (body) { setBody(null); return; }
    setBusy(true);
    setError("");
    try { setBody(await load()); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  }
  return <div className="runner-fingerprints">
    <Button size="sm" variant="secondary" loading={busy} aria-expanded={!!body} onClick={() => void toggle()}>
      {body ? "Hide full" : "Show full"}
    </Button>
    {error && <InlineAlert variant="error" title="Could not reveal contents" body={error} />}
    {body && <>
      <div>Input</div><pre className="workflow-content">{bodyText(body.input)}</pre>
      {!preview && <><div>Output</div><pre className="workflow-content">{bodyText(body.output)}</pre></>}
    </>}
  </div>;
}
