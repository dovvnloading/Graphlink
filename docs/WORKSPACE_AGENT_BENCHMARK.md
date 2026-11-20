# Opt-in workspace-agent benchmark

The live benchmark is deliberately **not part of** the default eval runner,
pytest's eval runner, or any CI workflow. It only runs when explicitly
invoked from a local shell with **--run**, and refuses to run when **CI** or
**GITHUB_ACTIONS** is present. **It makes real, potentially billable provider
calls.** It does not use mocks or scripted model turns.

From the repository root, in the normal Graphlink Python environment:

    # OPENAI_API_KEY must be set (or use a recognized provider-specific key).
    python -m backend.evals.workspace_benchmark --run --provider OpenAI --model gpt-4.1-mini --json-out benchmark.json

    # Local Ollama model; no cloud API key needed.
    python -m backend.evals.workspace_benchmark --run --provider Ollama --model qwen3:8b

    # For a custom OpenAI-compatible endpoint with an explicitly named key:
    python -m backend.evals.workspace_benchmark --run --provider OpenAI-Compatible --model my-model --base-url https://example.com/v1 --api-key-env MY_MODEL_API_KEY

**--provider** is a Graphlink provider display name (e.g. OpenAI,
Anthropic Claude, Google Gemini, OpenRouter, Ollama, or Llama.cpp).
For Llama.cpp, **--model** is the path to a configured local GGUF file.
For cloud providers, the regular Graphlink provider environment variables
are used; **--api-key-env** optionally chooses a specific variable without
putting its secret in a command-line argument. **--base-url** is only used for
cloud endpoints. It does **not** read or modify the app's saved provider
configuration. **--timeout** (seconds; default 120) and **--max-turns**
(default 12) cap each task.

The suite is **fixed** (four tasks, no cherry-picking):

1. **read_and_extract**: read a CSV and write the correct maximum's label.
2. **combine_two_files**: combine two CSV tables and write validated JSON totals.
3. **precise_edit**: read and edit one INI value without disturbing other bytes.
4. **search_and_deduplicate**: grep log files and write sorted unique incident IDs.

Each task runs the **production backend.harness.loop.run_harness** with a
real SceneDocument, ToolRegistry, RunRegistry, and filesystem handlers.
A fresh temporary workspace holds its input data and transcript. The
benchmark offers only fs.read, fs.list, fs.grep, fs.write, and
fs.edit; no shell, Python execution, MCP, web/network, graph mutation or
subagent tools. **--run** auto-approves only fs writes and edits to these
throwaway workspaces; any other approval is denied. Workspaces are removed
at the end. The grader reads the *actual files*, checks the required
successful tool invocations, and checks that unrelated seed files stayed
unchanged. A fluent final answer without correct artifacts fails.

A terminal table reports pass/fail, duration, input/output tokens, and USD
cost for every case, followed by pass rate and totals. Optional **--json-out**
exports the same metrics and failure details for comparisons. All runs are
sequential. The command exits 0 only if all tasks pass; 1 means at least
one task failed; 2 means setup or argument failure.

Token counts use provider-reported prompt/completion usage when available.
If a provider doesn't report a side, it is estimated from the messages,
tool declarations, final text and tool calls; the report shows how many
turns had fully reported usage. Costs are **estimates**, not billing data:
they use the repository's backend.token_counter.price_per_mtok table
(and $0 for local providers), and exclude hidden provider retries, caching
adjustments, infrastructure and external tool costs. Unknown model prices
show as **n/a, not $0**. For a known custom rate, specify **both**
**--input-price** and **--output-price** in USD per million tokens; these
explicitly override the built-in table for that invocation.

CI only tests the benchmark's *pure grader, accounting and opt-in guards*
through backend/tests/test_workspace_benchmark.py; it never calls the live
suite or makes provider requests.
