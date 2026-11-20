# Workspace agent: JSON tool calls without native tool support

The **Agent** workspace runner (and its read-only subagents) can use models
that do not implement native function/tool calling **if** their inference
runtime supports a constrained JSON response mode. This is a wire-protocol
fallback, not a different tool executor: the same tool names, scope checks,
user approvals, workspace confinement and transcript apply.

## Model paths

| Provider/model | Workspace tool protocol |
| --- | --- |
| llama.cpp / local GGUF | JSON Schema via llama-cpp-python's json_object response_format, compiled into a GBNF grammar |
| Ollama model without tools capability | JSON Schema via Ollama's format parameter |
| Custom OpenAI-compatible endpoint | Strict json_schema response_format, or json_object mode if the endpoint rejects the richer schema |
| Model with working native tools | Existing native tool calling (unchanged, except custom OpenAI-compatible endpoints use JSON proactively because native tool capability cannot reliably be probed) |

No separate workspace-agent setting is required. A nonnative model whose
runtime does not support structured JSON is **refused** with an actionable
error; the agent does not fall back to asking for unconstrained prose or
attempt to extract executable calls from arbitrary text. Other model and
endpoint errors still propagate normally.

**Builder** continues to require native tools. The JSON fallback is enabled
only by the workspace and subagent callers.

## Protocol and safety

For every model turn the adapter supplies available tool descriptions and
argument schemas plus an output schema. A tool turn looks like this:

```json
{
  "tool_calls": [
    {"name": "fs.read", "arguments_json": "{\"path\":\"notes.txt\"}"}
  ],
  "final_answer": ""
}
```

The result then goes through the existing ToolRegistry, and the next model
turn receives the actual tool result as **text**, not a native tool-role
message. To finish, the model returns an empty tool_calls array and a
nonempty final_answer string.

The outer JSON shape and tool names are constrained where supported, and
validated again before execution. The inner arguments_json field contains
a JSON-encoded **object**; this avoids strict OpenAI JSON Schema's
restriction on arbitrary object parameters while preserving each tool's
argument payload. Malformed JSON, unknown tools, mixed tool/final replies,
non-object arguments and more than eight calls in a turn are rejected.
Tool results are marked untrusted, and no result can bypass the registry's
approval checks. History is adapted per request; persisted transcripts
retain Graphlink's canonical assistant/tool entries.

This fallback is text-only. If the model cannot honor the structured JSON
mode, select a model/endpoint with native tools or supported structured
JSON output. Local grammar support depends on the installed llama.cpp or
Ollama runtime version and the configured model.
