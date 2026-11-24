import { useState } from "react";
import { Checkbox } from "../ui/Checkbox";
import { Select } from "../ui/Select";
import { TextArea } from "../ui/TextArea";
import { TextField } from "../ui/TextField";
import { isMcpObject, parseArguments, schemaFields, type McpBinding, type McpJson, type McpParent, type McpSchema } from "./mcpConnect";

export interface McpDraft {
  mode: "fields" | "json";
  text: string;
  fields: Record<string, string>;
  included: Record<string, boolean>;
  bindings: Record<string, McpBinding>;
}

export function createMcpDraft(schema: McpSchema, args: Record<string, McpJson>, bindings: Record<string, McpBinding>): McpDraft {
  const fields: Record<string, string> = {};
  const included: Record<string, boolean> = {};
  for (const field of schemaFields(schema)) {
    const value = args[field.name];
    fields[field.name] = value === undefined ? "" : field.schema.type === "string" && !Array.isArray(field.schema.enum)
      ? String(value) : JSON.stringify(value);
    included[field.name] = field.required || value !== undefined || !!bindings[field.name];
  }
  return { mode: Object.keys(fields).length ? "fields" : "json", text: JSON.stringify(args, null, 2), fields, included, bindings };
}

export function draftArguments(schema: McpSchema, draft: McpDraft): Record<string, McpJson> {
  if (draft.mode === "json") return parseArguments(draft.text);
  const args = parseArguments(draft.text);
  for (const field of schemaFields(schema)) {
    if (draft.bindings[field.name] || (!field.required && !draft.included[field.name])) {
      delete args[field.name];
      continue;
    }
    const raw = draft.fields[field.name] ?? "";
    if (field.schema.type === "string" && !Array.isArray(field.schema.enum)) args[field.name] = raw;
    else {
      try { args[field.name] = JSON.parse(raw); }
      catch { throw new Error(`Enter a valid ${field.schema.type ?? "JSON value"} for ${field.name}.`); }
      if ((field.schema.type === "number" || field.schema.type === "integer") &&
          (typeof args[field.name] !== "number" || !Number.isFinite(args[field.name]) ||
            field.schema.type === "integer" && !Number.isInteger(args[field.name])))
        throw new Error(`Enter a finite ${field.schema.type} for ${field.name}.`);
    }
  }
  // Validate finite numeric values and JSON depth on the same boundary as raw mode.
  return parseArguments(JSON.stringify(args));
}

function LiteralField({ name, schema, value, onChange, disabled }: {
  name: string; schema: McpSchema; value: string; onChange: (value: string) => void; disabled: boolean;
}) {
  const description = typeof schema.description === "string" ? schema.description : undefined;
  const label = typeof schema.title === "string" ? schema.title : name;
  if (Array.isArray(schema.enum)) return <Select label={label} value={value} disabled={disabled}
    placeholder="Choose a value…" options={schema.enum.map(item => ({
      value: JSON.stringify(item), label: typeof item === "string" ? item : JSON.stringify(item),
    }))} onChange={onChange} />;
  if (schema.type === "boolean") return <Select label={label} value={value} disabled={disabled}
    options={[{ value: "true", label: "True" }, { value: "false", label: "False" }]} onChange={onChange} />;
  if (schema.type === "string") return <TextField label={label} description={description}
    value={value} disabled={disabled} onValueChange={onChange} />;
  if (schema.type === "number" || schema.type === "integer") return <TextField
    label={label} description={description} value={value} inputMode="decimal" disabled={disabled}
    onValueChange={onChange} mono placeholder={schema.type === "integer" ? "Whole number" : "Number"} />;
  return <TextArea label={`${label} (JSON)`} description={description} value={value} disabled={disabled}
    onValueChange={onChange} minRows={2} maxRows={6} spellCheck={false}
    placeholder={schema.type === "array" ? "[]" : schema.type === "object" ? "{}" : "null"} />;
}

export function McpArgumentEditor({ schema, draft, onChange, parents, disabled }: {
  schema: McpSchema; draft: McpDraft; onChange: (next: McpDraft) => void; parents: McpParent[]; disabled: boolean;
}) {
  const [modeError, setModeError] = useState("");
  const fields = schemaFields(schema);
  function switchMode(mode: string) {
    try {
      const args = draftArguments(schema, draft);
      onChange({ ...createMcpDraft(schema, args, draft.bindings), mode: mode === "json" ? "json" : "fields" });
      setModeError("");
    } catch (error) { setModeError(error instanceof Error ? error.message : "Check the arguments before switching editors."); }
  }
  function bind(name: string, sourceId: string) {
    const bindings = { ...draft.bindings };
    if (!sourceId) delete bindings[name];
    else bindings[name] = { sourceId, output: parents.find(parent => parent.id === sourceId)?.outputs[0] ?? "text", pointer: "" };
    onChange({ ...draft, bindings, included: { ...draft.included, [name]: true } });
  }
  return <div className="mcp-arguments">
    {fields.length > 0 && <Select label="Argument editor" value={draft.mode} onChange={switchMode} disabled={disabled}
      options={[{ value: "fields", label: "Form fields" }, { value: "json", label: "JSON object" }]} />}
    {modeError && <p role="alert" className="mcp-help">{modeError}</p>}
    {draft.mode === "json" ? <>
      <TextArea label="Arguments (JSON object)" value={draft.text} disabled={disabled}
        onValueChange={text => onChange({ ...draft, text })} minRows={5} maxRows={12} spellCheck={false} />
      {Object.keys(draft.bindings).length > 0 && <p className="mcp-help">Connected inputs replace the matching top-level arguments when the tool runs.</p>}
    </> : fields.map(({ name, schema: fieldSchema, required }) => {
      const binding = draft.bindings[name];
      const parent = parents.find(item => item.id === binding?.sourceId);
      const included = required || draft.included[name] || !!binding;
      return <div className="mcp-argument-field" key={name}>
        <div className="mcp-argument-heading"><strong>{name}</strong>
          {required ? <span className="mcp-help">Required</span> : <Checkbox label={`Include ${name}`}
            checked={!!included} disabled={disabled} onChange={event => {
              const bindings = { ...draft.bindings };
              if (!event.target.checked) delete bindings[name];
              onChange({ ...draft, included: { ...draft.included, [name]: event.target.checked }, bindings });
            }} />}</div>
        {included && <>
          <Select label={`Input for ${name}`} value={binding?.sourceId ?? ""} disabled={disabled}
            options={[{ value: "", label: "Enter a value" }, ...parents.map(source => ({ value: source.id, label: source.title || source.kind })),
              ...(binding && !parent ? [{ value: binding.sourceId, label: "Disconnected source", disabled: true }] : [])]}
            onChange={sourceId => bind(name, sourceId)} />
          {binding ? <>
            <Select label={`Output for ${name}`} value={binding.output} disabled={disabled || !parent}
              options={(parent?.outputs ?? [binding.output]).map(output => ({ value: output, label: output === "json" ? "Structured JSON" : output === "table" ? "Table rows" : "Text" }))}
              onChange={output => onChange({ ...draft, bindings: { ...draft.bindings,
                [name]: { ...binding, output: output as McpBinding["output"], pointer: "" } } })} />
            {binding.output !== "text" && <TextField label={`JSON Pointer for ${name}`} value={binding.pointer} disabled={disabled}
              description="Leave empty for the whole output, or select a value such as /items/0/name."
              onValueChange={pointer => onChange({ ...draft, bindings: { ...draft.bindings, [name]: { ...binding, pointer } } })} mono />}
            {!parent && <p role="alert" className="mcp-help">Reconnect this source or choose another input before running.</p>}
          </> : <LiteralField name={name} schema={fieldSchema} value={draft.fields[name] ?? ""} disabled={disabled}
            onChange={value => onChange({ ...draft, fields: { ...draft.fields, [name]: value } })} />}
        </>}
      </div>;
    })}
    {fields.length > 0 && parents.length === 0 && <p className="mcp-help">Connect an upstream node to bind its text, JSON, or table output to a field.</p>}
    {(isMcpObject(schema.$defs) || !!schema.oneOf || !!schema.anyOf || !!schema.allOf) && <p className="mcp-help">
      This tool has a compound schema. Use the JSON editor for nested alternatives; the complete schema is validated before execution.</p>}
  </div>;
}
