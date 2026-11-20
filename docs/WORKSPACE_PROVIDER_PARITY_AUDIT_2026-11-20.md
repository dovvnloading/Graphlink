# Workspace Agent — cloud/local provider parity audit (2026-11-20)

## Scope and conclusion

Audited the merged workspace-agent invocation and execution path against six routes:
OpenAI (native Chat Completions), Anthropic (native Messages), Gemini (REST
GenerateContent), OpenAI-compatible (custom endpoint with schema-constrained JSON,
and named compatible services with native tools), Ollama (local native tools
or constrained JSON), and llama.cpp (local grammar-constrained JSON).

**Execution and permission policy is provider-neutral; model wire
protocol is not.** A normalized `ToolCall` passes through the same
`run_harness` / `ToolRegistry.invoke` gate regardless of transport. Cloud
functions use portable aliases for dotted names and recover original
names before approval or execution. Models lacking native function calling
must use generation-time schema/grammar constraints, not extract tool
commands from free-form prose.

This audit fixed concrete adapter defects. Recorded tests are deterministic
and require neither API keys, Ollama nor a GGUF; they **do not prove that
every model or remote endpoint supports every capability**.

## Tool inventory and common execution boundary

The workspace catalog is discovered from `backend/harness/tools_*.py`
plus shared graph/knowledge registrations. `run_harness` filters offered
`ToolSpec` entries by actual granted scopes, excludes Builder-only tools
and unrelated network tools, and enforces that offer again through
`RunContext.offered_tool_names`.

Merged tool families include workspace files, document/image reads and
writes, shell and persistent sessions, Python REPL and package installs,
plan/user interaction, transcript/skill search, subagents, knowledge search,
graph reads/mutations/publication, web search/fetch and enabled science
tools. Their specific permissions are enforced after model decoding by
the same registry and handler implementations, independent of provider.

## Findings and fixes

| Area | Finding before fix | Resolution |
| --- | --- | --- |
| Ollama tool results | Native replay dropped the tool's name, though Ollama tool messages support `tool_name`. | Preserve canonical tool name as Ollama `tool_name` while keeping Graphlink's correlation ID in its neutral transcript. |
| Ollama multi-call streaming | First `tool_calls` chunk was considered terminal even if `done:false`, losing later calls, token usage and truncation detection. | Accumulate calls across chunks and accept only `done:true`; preserve terminal usage. |
| llama.cpp streaming | Iteration without `finish_reason` silently committed partial output as successful. | Require a completion marker; release inference lock/close iterator on all exits. |
| Native tool arguments | OpenAI, Anthropic, Gemini and Ollama could pass non-object arguments; local conversion of arrays to dicts could reinterpret malformed inputs. | Use one common JSON-object type check before handing calls to approval/execution. |
| Shared turn dispatcher | A provider iterator lacking `done` could still yield a successful reply. | Require exactly one terminal event and reject events following `done`. |
| Workspace call budget | JSON fallback limited calls to eight per turn; native workspace calls did not. | Apply identical eight-call guard to native workspace responses. |

## Provider contract matrix

| Route | Tool request/call | Tool result replay | Stream completion | Model settings/caveats |
| --- | --- | --- | --- | --- |
| OpenAI | `tools[].function`, indexed JSON argument fragments, alias-to-canonical name conversion | Assistant `tool_calls` and `role:tool` with matching ID | `finish_reason`; normalized terminal `done` and optional usage | Model pin; chat-only reasoning; compatibility downgrade if `stream_options` unsupported |
| Anthropic | `tools[]` with `input_schema`, `tool_use` and incremental `input_json_delta` | User-role `tool_result` keyed by `tool_use_id` | `message_stop`; error SSE rejected | Model pin, cacheable system prompt, reasoning budget/effort |
| Gemini | `functionDeclarations` with `parametersJsonSchema`, complete `functionCall` with optional ID/signature | `functionResponse` and signed native call, when present | `finishReason`, error/safety filtering | Model pin, thinkingConfig, uploaded file cleanup |
| OpenAI-compatible | Native tools for supporting named services; custom endpoints prefer strict JSON tool turns | Native tool role or constrained JSON text history | OpenAI `finish_reason`; JSON-schema to JSON-object fallback only on explicit rejection | Endpoint- and model-dependent feature support |
| Ollama | Native function objects on probed models; else constrained JSON `format` | Native `role:tool` with `tool_name` or grouped untrusted text | `done:true`; multi-chunk calls and usage | Served `num_ctx`, reasoning-family flags |
| llama.cpp | Constrained JSON under `response_format`, not assumed native tools | Text-only transcript, no unsupported native roles | `finish_reason` required; streamed usage may be unavailable | Frozen/pinned GGUF, inference lock, chat-template thinking |

All six normalize to the same tool-call ID/name/dict-arguments and
produce the same result text (including denied or handler-error messages)
and durable transcript. Synthetic IDs for local/JSON calls correlate results
without implying native endpoint support for IDs. Permission equivalence
does not imply identical vision/audio/embedding/image features.

## Recorded evidence and regression checks

`backend/tests/fixtures/workspace_provider_parity.json` has synthetic
transport-shaped responses for **all six routes** and a two-turn function
request → denied/approved write → model answer. The corresponding
`backend/tests/test_workspace_provider_parity_recordings.py` invokes
the real provider adapters and `api_provider.chat_turn_with_tools` (not
a mocked Provider), verifies aliases, usage, relevant model settings,
JSON fallback, approval denial/allowance, tool-result history replay,
and missing terminal markers. It additionally tests Ollama calls in
successive stream chunks and non-object native tool arguments. Frames
represent API wire contracts, not a captured live provider trace.

`backend/tests/test_workspace_provider_catalog_matrix.py` also derives the full
currently offered tool inventory from the live registry and verifies its
JSON-name set, cloud aliases/schema preservation and pre-approval scope gate.

Existing focused tests cover the shared underlying tools and settings:
`test_harness.py`, `test_harness_tool_catalog.py`,
`test_harness_permission_modes.py`, `test_harness_network_approval.py`,
`test_harness_file_approval_preview.py`, `test_harness_python_install.py`,
`test_harness_subagents.py`, `test_harness_web_tools.py`,
`test_harness_scientific_tools.py`, `test_tool_registry.py`,
`test_json_tool_protocol.py`, `test_tool_calling.py`,
`test_providers.py`, `test_provider_stream_completion.py`,
`test_llama_cpp_model_pin.py` and `test_cloud_provider_runtime.py`.

Suggested local check from repository root:

`python -m pytest -q backend/tests/test_workspace_provider_parity_recordings.py backend/tests/test_tool_calling.py backend/tests/test_json_tool_protocol.py backend/tests/test_provider_stream_completion.py backend/tests/test_harness_tool_catalog.py`

The `AGENTS.md` CI contract remains in force, including Windows,
POSIX permissions, full pytest/coverage, Ruff and frontend checks.

**Execution status:** The deterministic test cases and fixes were authored
through the GitHub repository integration. A local checkout/runtime was not
available for running pytest or Ruff here, so no local passing-test claim is
made. The PR's CI is the next validation gate; it was not waited on.

## Limits and future verification

- Recorded responses verify the adapter and authorization contract given
  those frames. They do not replace integration testing against current
  live APIs, an Ollama model or an actual llama.cpp GGUF.
- OpenAI-compatible services may reject both native tools and constrained
  output; the existing behavior fails with actionable model guidance, never
  by executing tool calls inferred from prose.
- Image/audio/reasoning capabilities are model-specific; workspace image
  observations are capability-gated rather than guaranteed on every route.
- OS-specific shell/Python sandboxing and UI approval reviews are covered by
  separate platform/browser tests; provider recordings verify their shared
  policy entry point, not the underlying OS controls.
