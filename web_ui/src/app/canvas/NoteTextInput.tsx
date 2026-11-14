import { useId, useState, useSyncExternalStore } from "react";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
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

function OperationSelect({ value, onChange }: { value: TextInputOperation; onChange: (operation: TextInputOperation) => void }) {
  return (
    <label>Operation
      <select value={value} onChange={(event) => onChange(event.target.value === "word_count" ? "word_count" : "copy")}>
        <option value="copy">Copy text</option>
        <option value="word_count">Word count</option>
      </select>
    </label>
  );
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
      <label>Text source
        <select value={selected} onChange={(event) => setSelection(event.target.value)}>
          <option value="">{candidates.length ? "Choose a source…" : "No available source"}</option>
          {candidates.map((node) => <option key={node.id} value={node.id}>{textSourceLabel(node)}</option>)}
        </select>
      </label>
      {candidates.length === 0 && <p>Add a note, chat message or Drafter to supply text.</p>}
      <OperationSelect value={operation} onChange={setOperation} />
      <button type="submit" disabled={!selected}>Connect</button>
    </form>
  );
}

function BoundTextInput({ textInputSourceId, textInputSourceLabel, textInputOperation, textInputStatus, onBindTextInput, onRunTextInput, onUnbindTextInput }: NoteTextInputProps) {
  const [runStatus, setRunStatus] = useState<"idle" | "running" | "failed">("idle");
  const freshness = textInputStatus === "stale" ? "Source changed · rerun to update"
    : textInputStatus === "current" ? "Input up to date" : "Ready to run";
  return (
    <>
      <div className="note-text-input-source" title={textInputSourceLabel}>Text from {textInputSourceLabel}</div>
      <div className="note-text-input-fields">
        <OperationSelect value={textInputOperation} onChange={(operation) => onBindTextInput(textInputSourceId, operation)} />
        <p>Running replaces this note’s text.</p>
        <div className="note-text-input-actions">
          <button type="button" aria-label="Run text input" disabled={runStatus === "running"} onClick={() => {
            setRunStatus("running");
            onRunTextInput((success) => setRunStatus(success ? "idle" : "failed"));
          }}>Run</button>
          <button type="button" aria-label="Disconnect text input" onClick={onUnbindTextInput}>Disconnect</button>
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
            <button className="note-text-input-toggle" type="button" aria-label="Text input" aria-expanded={open} aria-controls={pickerId}
              onClick={() => setOpen(!open)}>Text input</button>
            <div id={pickerId} hidden={!open}>{open && <SourcePicker {...props} />}</div>
          </>}
    </section>
  );
}
