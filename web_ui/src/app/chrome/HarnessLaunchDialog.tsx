import { useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneStore } from "../canvas/sceneStore";
import { motionDuration } from "../reducedMotion";
import { Dialog, useOverlays } from "../overlays/overlays";
import {
  HarnessExecutionProfileSelect, MAX_HARNESS_TASK_LENGTH, exceedsHarnessTaskLimit, type HarnessExecutionProfile,
} from "./HarnessLaunchOptions";
import { Button } from "../ui/Button";
import { FieldGroup } from "../ui/Field";
import { InlineAlert } from "../ui/InlineAlert";
import { NumberField } from "../ui/NumberField";
import { TextArea } from "../ui/TextArea";
import "./BuilderLaunchDialog.css";

/**
 * PLAN-2026-08-24 H1: the workspace agent's launcher - a task and a turn
 * budget, submitted through `harness/start` (a value-returning intent
 * answering with the new node's id - the BuilderLaunchDialog precedent
 * exactly, including the close-then-center-viewport sequence; see that
 * dialog's own comments for why request(), not fireIntent, and why the
 * center is approximate).
 *
 * Deliberately smaller than the Builder's launcher: no recipes, no
 * oversight modes (the grant set is fixed and every mutating tool asks
 * for approval individually, so there is no oversight LEVEL to choose),
 * one budget number and one workspace choice.
 *
 * The workspace is chosen HERE rather than only on the node afterwards:
 * binding a folder after the fact meant letting a scratch run finish,
 * rebinding, and re-sending the same task, so the first run of every
 * real piece of work was wasted. The pick itself is the grant (the
 * backend adds the folder to the trust list when the picker returns),
 * and the run re-checks that list when it binds.
 */

const DEFAULT_MAX_TURNS = 16;
// Mirrors intents_harness.py's own _MIN/_MAX_TURNS exactly - the backend
// clamps regardless; the field should settle on what it will submit.
const MIN_TURNS = 1;
const MAX_TURNS = 64;

export function HarnessLaunchDialog({ transport, store }: { transport: WsTransport; store: SceneStore }) {
  const overlays = useOverlays();
  const reactFlow = useReactFlow();
  const [task, setTask] = useState("");
  const [maxTurns, setMaxTurns] = useState<number>(DEFAULT_MAX_TURNS);
  const [workspace, setWorkspace] = useState("");
  const [executionProfile, setExecutionProfile] = useState<HarnessExecutionProfile>("host");
  const [picking, setPicking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequestId = useRef(0);

  function pickWorkspace() {
    if (picking || starting) return;
    setPicking(true);
    transport
      .request("harness", "pickLaunchWorkspace", [])
      .then((folder) => {
        if (typeof folder === "string" && folder) setWorkspace(folder);
      })
      .catch(() => setError("Could not open the folder picker."))
      .finally(() => setPicking(false));
  }

  function startAgent() {
    const trimmed = task.trim();
    if (!trimmed || starting) return;
    if (exceedsHarnessTaskLimit(trimmed)) {
      setError(`Agent tasks cannot exceed ${MAX_HARNESS_TASK_LENGTH.toLocaleString()} characters. Shorten the task; your draft is preserved.`);
      return;
    }
    const requestId = ++latestRequestId.current;
    setStarting(true);
    setError(null);
    transport
      .request("harness", "start", [trimmed, maxTurns, workspace, executionProfile])
      .then((nodeId) => {
        if (requestId !== latestRequestId.current) return;
        if (nodeId != null) {
          setTask("");
          setWorkspace("");
          setExecutionProfile("host");
          overlays.close();
          const node = store.getScene().nodes.find((n) => n.id === nodeId);
          if (node) {
            reactFlow.setCenter(node.x + 180, node.y + 90, { duration: motionDuration(300) });
          }
        } else {
          setError("The agent could not start - see the notification for details.");
        }
      })
      .catch(() => {
        if (requestId !== latestRequestId.current) return;
        setError("The agent could not start - see graphlink.log for details.");
      })
      .finally(() => {
        if (requestId === latestRequestId.current) setStarting(false);
      });
  }

  return (
    <Dialog name="harness-launch" {...{ title: "Agent" }} size="md" className="builder-launch-dialog"
      status={!task.trim() ? "Describe a task to start." : "Actions that change your machine require approval."}
      footer={<Button variant="primary" disabled={starting || !task.trim()} loading={starting}
        onClick={startAgent}>Start agent</Button>}>
      <TextArea id="harness-task" label="What should the agent work on?"
        value={task} onValueChange={setTask} minRows={3} maxRows={10}
        placeholder="Read the files in the workspace and summarize what they contain"
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            startAgent();
          }
        }}
        description="The agent reads and writes workspace files, runs shell and Python, and searches your knowledge. It asks before changing your machine." />
      <FieldGroup {...{ title: "Workspace" }} description="Choose a folder to grant access, or use a private scratch folder.">
        <div className="harness-launch-workspace">
          <span className="harness-launch-workspace-dir">{workspace || "Private scratch folder"}</span>
          <Button size="sm" onClick={pickWorkspace} disabled={picking || starting} loading={picking}>
            Choose folder…
          </Button>
          {workspace && <Button size="sm" variant="ghost" onClick={() => setWorkspace("")} disabled={starting}>
            Use scratch
          </Button>}
        </div>
      </FieldGroup>
      <FieldGroup {...{ title: "First task execution" }}>
        <HarnessExecutionProfileSelect store={store} value={executionProfile}
          onChange={setExecutionProfile} disabled={starting} />
      </FieldGroup>
      <FieldGroup {...{ title: "Budget" }} description="Hard limit — the run stops when it is reached.">
        <NumberField label="Max turns" min={MIN_TURNS} max={MAX_TURNS}
          value={maxTurns} onValueChange={(value) => setMaxTurns(value === "" ? DEFAULT_MAX_TURNS : value)} />
      </FieldGroup>
      {error && <InlineAlert variant="error" {...{ title: "Couldn't start the agent" }} body={error} />}
    </Dialog>
  );
}
