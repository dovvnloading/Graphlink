# Workspace agent user guide

The **Agent** works on files and tasks from a card on the Graphlink canvas. Unlike **Builder**, which plans and edits the graph, Agent can inspect a project, run approved shell/Python tools, and place results back on the canvas. You stay in control of workspace access and tool approvals.

## Start an agent

1. [Connect a model](../README.md#connect-a-model), either a local Ollama/llama.cpp model or a hosted API provider.
2. Open **Agent** (also available from the command palette as **Open Agent**). Describe a concrete task, such as “Summarize the Python modules in this folder and put the findings in a note.”
3. Choose **Private scratch folder** (the default), or **Choose folder…** to grant access to an existing project. Picking a folder grants that resolved directory to the Agent; pasting a path into a saved graph does **not** grant it.
4. Set **Max turns** if needed (default 16; allowed range 1–64), then choose **Start the agent**. The turn budget is a hard stop, not an estimated time.
5. Follow the Agent card’s status, checklist, activity and tool-call details. Review any proposed tool call and select **Approve once** or **Deny**. When the run ends, use **Send a follow-up…** to continue in the same Agent; **Stop** cancels a running task.

The Agent may ask a specific question in a separate **Answer** panel. Answering that question is different from sending the next task. **Undo run** reverses recorded canvas edits from that run; it is **not** a rollback of changes made to files or packages on disk. Save the graph to retain the Agent card and history.

## Choose a model: cloud or local

| Model route | Setup | What to expect |
| --- | --- | --- |
| **Ollama (local)** | Start Ollama, pull a chat model, and configure **Settings → Ollama (Local)**. | Inference runs through your local Ollama server. Models without native tool support can use Ollama’s constrained JSON mode when supported by the model/runtime. |
| **Llama.cpp (local GGUF)** | Install `llama-cpp-python` in Graphlink’s application Python environment, select a GGUF in **Settings → Llama.cpp (Local)**, and activate it. | Local inference uses llama.cpp; JSON tool calls require its supported grammar/JSON mode. |
| **Cloud provider or custom endpoint** | Select **Settings → API Endpoint**, configure provider credentials and model ID, then **Save and activate**. | The selected provider receives the model request and relevant task context. Actual tool calling and structured-output support vary by endpoint and model. |

The Agent normally uses the configured chat route. When you send to an Agent through the **main composer**, it uses the exact available model shown in that composer for that task. The Agent launcher and the follow-up input on its card use their existing/default route. Check the active connection before launching a task if model choice matters.

**Tool support:** Native function calling works where the model supports it. For some models without native tools, Agent automatically uses a constrained **JSON tool protocol**: Ollama format, llama.cpp grammar, or a compatible endpoint’s JSON response format. This is text-only and uses the **same tool permissions and approval prompts**. If the model/runtime has neither usable native tools nor constrained JSON, choose another model; Agent does not execute tool requests extracted from free-form prose. Builder still requires native tools. See [JSON tool protocol](WORKSPACE_JSON_TOOLS.md).

Local inference can work offline after installing its runtime and models, but **web research, package downloads, and other approved online integrations can still connect to the network**. Hosted inference is not local or private/offline.

## Workspace folders and files

Each task binds to **one workspace root**:

- **Scratch:** An app-managed private folder, reused by that Agent while it exists. This is a good starting point for experiments.
- **Chosen project:** A folder explicitly granted through **Choose folder…**, including at Agent launch. Agent filesystem and execution tools operate relative to that root.
- **Access changes:** Use **Use scratch** to switch future tasks back to scratch. In **Settings → Workspace Agent → Trusted Folders**, select **Revoke** to prevent *new runs* from using a granted folder. A running task keeps its already-bound root until it ends. An untrusted or missing folder in a reloaded/imported graph falls back to scratch rather than silently gaining access.

Paths are confined to the resolved workspace root: `..` traversal and symlinks cannot be used to reach outside it. Git, Mercurial and Subversion metadata directories are excluded from the Agent’s filesystem browser/tools. Changing workspace while the Agent is running is disabled; stop the task first.

The Agent card has **Browse files** and **Reveal folder**. Browse opens folders one at a time and opens supported files in Document View (a **preview**, not an editor); Reveal folder opens the actual root in your file manager. For work done by the model:

| Tool | Use |
| --- | --- |
| `fs.list` / `fs.grep` | Find files and search text inside the workspace. |
| `fs.read` | Read text or supported images and extract text from PDF, Word `.docx`, Excel `.xlsx` / `.xlsm`. |
| `fs.write` / `fs.edit` | Create, replace or edit files after approval. Review the proposed content or unified diff; if no safe preview is available, deny and ask the Agent to retry. |

For `fs.read`, use `page_start` / `page_end` for inclusive, 1-based PDF pages; `sheet` or `sheet_start` / `sheet_end` for Excel; and `offset` / `limit` to window text lines. Legacy `.doc` / `.xls` are unsupported. Image-only PDFs are not OCRed; Excel formulas and macros are not executed. Reads are bounded (8 MiB source files and 40,000-character results), so request a narrower range for large documents. [Document-reading details](WORKSPACE_DOCUMENT_READS.md).

## Python: execution, environments and installs

Ask the Agent to use **`python.exec`** for analysis, scripts or data transformations. It is a **persistent Python interpreter**: imports and variables survive between calls in the same Agent/workspace/profile until the interpreter is retired. Use `print()` to display results; a bare expression is not echoed. **Every execution needs fresh approval**, even if the same code was approved before. Host Python has your user account’s permissions and is **not a sandbox**.

The interpreter is selected automatically; the model cannot choose an arbitrary environment:

| Where the Agent works | Python environment |
| --- | --- |
| **Host + chosen project** | First usable `.venv`, then `venv` inside the project. Otherwise an app-managed virtualenv scoped to this Agent **and** project, outside your project directory. |
| **Host + scratch** | A lazily created app-managed virtualenv inside the Agent’s scratch workspace. |
| **Isolated profile** | Container-native Python: a compatible Linux project `.venv` / `venv` if present, otherwise a separate workspace-scoped container virtualenv. Host virtualenvs and host Python are never run inside the container. |

For dependencies, ask the Agent to call **`python.install`**, for example with `{"packages": ["requests>=2,<3", "pandas==2.2.3"]}`. It installs into the **same interpreter** that `python.exec` uses. The approval panel identifies that interpreter and **every requested requirement**. **Each installation requires its own new approval**; earlier approvals and saved shell rules cannot authorize a later install. The tool accepts named PyPI packages, extras and version constraints—not arbitrary URLs, VCS repositories, requirements files, local paths or pip flags. Binary wheels still contain executable code, so inspect the packages before approving.

Installs try a shared local binary-wheel cache first (`~/.graphlink/python-wheel-cache/wheels`). If suitable wheels/dependencies are missing, the tool downloads *binary wheels only* from the canonical PyPI index and performs the final installation **offline from the cache**. Cached installs may work offline; an uncached wheel requires network access and an available compatible build. In Isolated mode, only the freshly approved PyPI **download phase** gets container networking; environment setup, code execution and the install phase remain network-disabled. For full restrictions and consent details, see [Python package installation](PYTHON_INSTALL.md).

## Shells, sessions and remembered allow rules

In an Agent card, open **Agent settings** (gear icon) and select a **Shell**. On Windows the choices are **PowerShell** (default), **Command Prompt (cmd)** and **Git Bash** (requires Git for Windows); on macOS/Linux it uses **Bash**. The setting applies to the **next task**, not a command already running. In the Isolated profile, shell commands instead execute in the Linux container using `/bin/sh`.

- **`shell.exec`** runs a one-shot command in the bound workspace and requests permission for the command. It has a 120-second hard timeout.
- **`shell.session`** manages a long-running named process, such as a development server: start, read output, write stdin, list and stop. Starting a session and sending input require fresh consent; saved `shell.exec` rules do not authorize interactive stdin.
- The approval panel discloses proposed shell commands, including command segments in a chain. For safe, unambiguous `shell.exec` commands, **Always allow** stores a **shell-specific command prefix on that Agent**; later commands can skip the prompt only if **every segment** matches an approved prefix at an argument boundary. This is **not** a blanket “approve all shell” grant.
- Dangerous or ambiguous commands, redirections and inline interpreter programs cannot create remembered prefixes. They still need explicit per-call approval. Remove a saved prefix any time under **Agent settings → Allowed command prefixes → Remove**.

Prefer **Approve once** when a command might modify important data or its effect is unclear. Shell rules are not security sandboxes; Host execution still runs under your account. The rule system and guard cannot make arbitrary shell commands harmless.

## Canvas and scientific tools

Agents can work with the existing graph as well as files:

- **`graph.list_nodes`** and **`graph.read_subgraph`** inspect the canvas; **`graph.create_node`**, **`graph.connect`**, **`graph.set_node_content`**, **`graph.delete_node`** and supported **`run_node`** actions can update or run graph content with the usual approvals. Deletion requires explicit approval.
- **`graph.publish_file`** copies an **existing workspace file** beside the Agent and connects the new node to it. Supported files: PNG/JPEG/GIF/WebP images, CSV or Excel `.xlsx` / `.xlsm` tables (optional `sheet` name), and Markdown or plain UTF-8 text documents. Published contents are a **copy**, not a live link to the file; the canvas node is part of the run’s undo history.
- **`plan.update`** maintains the Agent’s checklist; **`user.ask`** pauses for an answer. `session.search` can search that Agent’s saved transcript when earlier details have been compacted.

For verified calculations, enable **Scientific Core** and the needed `science_<pack>` entry in **Settings → Plugins**. The Agent can call **`science.search`** to discover operations and required inputs from *enabled* packs, and **`science.run`** to execute a chosen operation after approval. A successful run creates a linked Workbench node and returns actual values, summary and a verified provenance receipt. Disabled packs are not exposed; invalid input or a failed/cancelled calculation does not create a result node. Agent calls supply their own literal inputs—not implicit graph wiring. For connected pipelines use the Workbench UI and Graph Runner. [Scientific Agent tools](WORKSPACE_SCIENTIFIC_TOOLS.md) · [Workbench guide](SCIENTIFIC_WORKBENCH.md).

## Isolated execution profile

**Host** is the default execution profile. To move shell and Python execution into a restricted environment, open the Agent’s **Agent settings → Execution profile → Isolated (Docker / Podman)**.

**First-task caveat:** The launcher starts a new Agent’s first task immediately and does not yet offer a profile selector. That initial task uses **Host**. If you need Isolated for your real work, start with a harmless read-only setup task, deny any Host shell/Python requests, and switch profiles before sending the next task.

1. Install and start **Docker** or **Podman** on the backend machine. Pull the fixed image in advance:

   ```sh
   docker pull python:3.12-slim
   # or
   podman pull python:3.12-slim
   ```

2. Select **Isolated** in the Agent settings. The preference is saved with the graph and applies to **subsequent tasks**. If a container engine is not detected, the UI disables that choice.
3. Run the task and review approvals as usual. If the engine, daemon or image becomes unavailable, execution **fails closed** instead of falling back to Host.

The container mounts the **bound scratch/project workspace** at `/workspace` and uses a read-only image filesystem, a small temporary filesystem, 1 CPU, 512 MiB RAM/swap, a 128-process limit, dropped capabilities and no-new-privileges. Container shell and Python operations have **no network** by default. Container-native virtualenvs and the installer’s cache are mounted only where those Python operations require them. Host executables and backend secrets are not passed through.

**Isolated limits command execution, not the whole Graphlink application.** File browsing/edits and graph tools still run through the backend under their own workspace and approval rules. Cloud model calls, approved Web Research and separately approved integrations (such as Fal) can still use the backend’s host network. An approved `python.install` PyPI download is the sole container-command exception to the network-disabled default. Isolation is not a guarantee against a malicious container engine/image. [Execution profiles and network boundaries](WORKSPACE_ISOLATION.md).

## Web research and network approval

When the built-in **Web Research** plugin is enabled, the Agent can use **`web.search`** (DuckDuckGo) and **`web.fetch`** (public HTTPS pages). These are **backend** services, not shell commands inside the container.

- The first `web.search` call in a **task** asks permission for the DuckDuckGo search-service family; later searches in that task reuse the approval. This is service-family consent, not a low-level hostname firewall.
- `web.fetch` validates public destinations and follows the Web Research fetch policy (including robots/crawl controls). It asks once **per destination hostname, per task**, including every new hostname reached by a redirect. Approving one site does not approve unrelated domains or future tasks.
- The Agent is deliberately offered only `web.search` and `web.fetch` from this network-tool category. It cannot use `run_node` to bypass those per-domain checks by executing a Web Research node’s `research` action.

Search results, pages and skill content are **untrusted reference data**, not executable instructions. Model-authored queries or URLs may disclose information to a domain you approve, so check the requested destination and avoid including confidential text in searches. The canvas Web Research node has a separate evidence-report workflow; see [Web Research](WEB_RESEARCH.md).

## Reusable skills

Skills are instructions you add to disk, not plugins or permission grants. Put each package’s `SKILL.md` in either:

- **This workspace only:** `.graphlink/skills/<package>/SKILL.md` under the chosen/scratch root.
- **All your Agents:** `~/.graphlink/skills/<package>/SKILL.md` under your user home.

For example:

```markdown
---
name: test-project
description: Find the project's test commands and explain how to run them.
---

Inspect the project README and test configuration before proposing a command.
Summarize any risks and ask for tool approvals as usual.
```

Every skill needs YAML frontmatter with **`name`** and **`description`**. Agent prompts receive only that metadata. When a skill is relevant, the Agent loads its full instructions with **`skills.load`**, identifying the **workspace** or **user** source and skill name. Colliding names retain their source labels. Skill instructions **cannot** expand workspace access, bypass tool approval, or override safety rules. Treat skills copied from strangers as untrusted text.

## When something does not work

| Symptom | Check |
| --- | --- |
| The Agent says the model cannot call tools | Choose a model with native tool calling or a runtime supporting constrained JSON output. |
| Agent reads scratch instead of a selected project | Re-select the folder with **Choose folder…**; check **Settings → Workspace Agent → Trusted Folders**. Imported paths do not grant access automatically. |
| Python cannot import a package | Confirm the intended workspace/profile; use approved `python.install` so it targets `python.exec`'s interpreter. An incompatible wheel may not be available. |
| Isolated commands cannot start | Start Docker/Podman and pre-pull `python:3.12-slim`; the Agent will not silently use Host. |
| The Agent keeps asking to approve a command | Dangerous/ambiguous shell commands, `python.exec`, `python.install` and interactive stdin still require fresh approval; **Always allow** applies only to eligible `shell.exec` prefixes. |
| A web page needs another approval | A new task or a redirected-to hostname gets a new domain consent prompt. |
| A document read is empty or truncated | Try a narrower page/sheet/text range; scanned PDFs require OCR outside `fs.read`. |

For further implementation-specific details, see [Python installs](PYTHON_INSTALL.md), [Isolated profiles](WORKSPACE_ISOLATION.md), [document reads](WORKSPACE_DOCUMENT_READS.md) and the [tool protocol](WORKSPACE_JSON_TOOLS.md).
