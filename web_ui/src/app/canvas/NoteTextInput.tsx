import { useId, useState, useSyncExternalStore } from "react";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";
import { Tooltip } from "../ui/Tooltip";
import type { SceneStore } from "./sceneStore";
import { availableTextSources, textSourceLabel, type TextInputOperation } from "./textInputs";

export interface NoteTextInputProps {
  targetId: string;
  textInputSourceId: string;
  textInputSourceLabel: string;
  textInputOperation: TextInputOperation;
  textInputStatus: SceneNodeRow["textInputStatus"];
  subscribeScene: SceneStore["subscribe"];
  getScene: SceneStore["getScene"];
  onBindTextInput: (sourceId: string, operation: TextInputOperation) => void;
  onRunTextInput: (onSettled: (success: boolean) => void) => void;
  onUnbindTextInput: () => void;
}

const OPERATIONS = [
  { value: "copy", label: "Copy text" },
  { value: "word_count", label: "Word count" },
] as const;

function OperationSelect({ value, onChange }: { value: TextInputOperation; onChange: (operation: TextInputOperation) => void }) {
  return <Select label="Operation" value={value} options={OPERATIONS}
    onChange={(next) => onChange(next === "word_count" ? "word_count" : "copy")} />;
}

// Only an open picker subscribes to candidate sources. An edit or deletion
// in another window is reflected here before the user presses Connect.
function SourcePicker({ targetId, subscribeScene, getScene, onBindTextInput }: NoteTextInputProps) {
  const scene = useSyncExternalStore(subscribeScene, getScene);
  const candidates = availableTextSources(scene.nodes, targetId);
  const [selection, setSelection] = useState("");
  const [operation, setOperation] = useState<TextInputOperation>("copy");
  const selected = candidates.some((node) => node.id === selection) ? selection : "";
  return (
    <form className="note-text-input-fields" onSubmit={(event) => {
      event.preventDefault();
      if (selected) onBindTextInput(selected, operation);
    }}>
      <Select label="Text source" value={selected} onChange={setSelection}
        placeholder={candidates.length ? "Choose a source…" : "No available source"}
        options={candidates.map((node) => ({ value: node.id, label: textSourceLabel(node) }))} />
      {candidates.length === 0 && <p>Add a note, chat message or Drafter to supply text.</p>}
      <OperationSelect value={operation} onChange={setOperation} />
      <Button variant="primary" size="sm" type="submit" disabled={!selected}>Connect</Button>
    </form>
  );
}

function BoundTextInput({ textInputSourceId, textInputSourceLabel, textInputOperation, textInputStatus, onBindTextInput, onRunTextInput, onUnbindTextInput }: NoteTextInputProps) {
  const [runStatus, setRunStatus] = useState<"idle" | "running" | "failed">("idle");
  const freshness = textInputStatus === "stale" ? "Source changed · rerun to update"
    : textInputStatus === "current" ? "Input up to date" : "Ready to run";
  return (
    <>
      <Tooltip label={textInputSourceLabel}>
        <div className="note-text-input-source">Text from {textInputSourceLabel}</div>
      </Tooltip>
      <div className="note-text-input-fields">
        <OperationSelect value={textInputOperation} onChange={(operation) => onBindTextInput(textInputSourceId, operation)} />
        <p>Running replaces this note’s text.</p>
        <div className="note-text-input-actions">
          <Button variant="primary" size="sm" iconStart="play" aria-label="Run text input"
            disabled={runStatus === "running"} onClick={() => {
              setRunStatus("running");
              onRunTextInput((success) => setRunStatus(success ? "idle" : "failed"));
            }}>Run</Button>
          <Button variant="ghost" size="sm" aria-label="Disconnect text input"
            onClick={onUnbindTextInput}>Disconnect</Button>
        </div>
        <span className="note-text-input-status" role="status" data-state={textInputStatus}>
          {runStatus === "running" ? "Running…" : runStatus === "failed" ? "Run failed" : freshness}
        </span>
      </div>
    </>
  );
}

export function NoteTextInput(props: NoteTextInputProps) {
  const [open, setOpen] = useState(false);
  const pickerId = useId();
  return (
    <section className="note-text-input nodrag nopan nowheel" aria-label="Note text input">
      {props.textInputSourceId
        ? <BoundTextInput key={`${props.textInputSourceId}/${props.textInputOperation}`} {...props} />
        : <>
            <Tooltip label="Connect text from a different node to this note. Running the input replaces this note’s text.">
              <Button className="note-text-input-toggle" variant="ghost" size="sm" iconStart="text-cursor"
                aria-expanded={open} aria-controls={pickerId}
                onClick={() => setOpen(!open)}>Add text input</Button>
            </Tooltip>
            <div id={pickerId} hidden={!open}>{open && <SourcePicker {...props} />}</div>
          </>}
    </section>
  );
}
