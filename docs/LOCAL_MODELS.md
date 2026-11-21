# Local models: Ollama and llama.cpp

Graphlink can run chat and agent tasks using models on your own computer, without a hosted model API key. **Ollama** runs models through a local server; **llama.cpp** runs a selected GGUF file through Graphlink's Python environment. Each has its own page under **Settings**. If you are using another provider, select **Use This Provider** on the relevant local page to make it the active provider mode.

## Get Ollama running

1. Open **Settings → Ollama (Local)**. The **Ollama server** indicator reports **Checking...**, **Running**, **Installed but not running**, or **Not found**.
2. If it says **Installed but not running**, select **Start Ollama**. Graphlink launches the already-installed app or executable and waits a bounded time for the server to respond. On Windows, if the installed tray app does not start the server, Graphlink tries the installed `ollama.exe serve` CLI. If neither attempt succeeds, it reports that Start failed and suggests starting Ollama manually; status checks continue automatically.
3. If it says **Not found**, **install Ollama yourself** from [Ollama's official download page](https://ollama.com/download), using the installer for your operating system. Graphlink does not download or install the Ollama runtime. Start the Ollama app, or run `ollama serve` if you use its command-line server, then return to Settings.
4. When the status becomes **Running**, Graphlink automatically refreshes the server-verified model inventory (including after starting Ollama manually), replacing disk-only unknown metadata. The Ollama settings page also scans on open; **Refresh installed models** (or **System Scan**) remains available for an explicit refresh.

A running server is not the same as an installed model: a new installation may show **No models installed in this Ollama instance**. Graphlink reads the running server's installed tags and model details. If the server is unavailable, **System Scan** can fall back to finding model names on disk; **Scan Folder...** lets you choose a folder manually. Disk-only results are *not* verified server inventory: start Ollama and scan again to inspect capabilities, load state and memory, and to enable removal.

## Download and remove Ollama models in Settings

Under **Download recommended models**, choose **Pull** beside a suggested model. The starter choices include `qwen3:4b` (tool-capable chat), `nomic-embed-text:latest` (embeddings), and `gemma3:4b` (vision/image understanding). Displayed download sizes are approximate, and **nothing is downloaded until you select Pull**. These are suggestions, not required defaults.

For another Ollama tag, enter its exact name in **Validate and Pull Model**, then select the matching button. This is an advanced download field, **not** a requirement to type a custom model ID just to use a model that is already installed. Downloads show a status, per-layer progress and **Cancel download**. A failed or unknown tag produces a notice; check that Ollama is running and that the tag exists. You can also install a tag outside Graphlink with `ollama pull MODEL` and then refresh Settings.

Once a download completes, refresh the inventory if needed and pick a **Chat Model** below. Pulling a model can update the current runtime selection, including when the new tag is embedding-only, so check the chat assignment before sending a message.

To delete a model, find its card under **Local model inventory**, choose **Remove**, and then **Confirm remove** (or **Keep model** to back out). This deletes the local Ollama model files, not just the Settings entry. Removal requires a reachable server and a verified server-inventory card; disk-only scan results cannot be removed from Graphlink. An explicit chat assignment to a removed model returns to **Auto**, and other tasks assigned to it return to **Use chat model**.

## Read the model cards and capability badges

Each installed-model card may show:

- The **model name** and **Loaded**, **Not loaded**, or **Load state unavailable**.
- **Size on disk**, **family**, **parameter size** and **quantization**, when Ollama provides them.
- **Trained context** length, or **Unverified** if it cannot be confirmed from the running server.
- **Memory in use** and **VRAM** for a loaded model when that information is available.
- Capability badges such as **chat**, **tools**, **vision**, **embeddings** and **thinking**. These come from Ollama's model metadata, not from guesses based on the model name.

**Capabilities unavailable** means the metadata was not returned, **not** that the model definitely lacks those capabilities. Folder-scan cards explicitly say **Disk scan only**; refresh with the server running for verified details. An embedding-only model is useful for Knowledge embeddings but cannot be selected as a chat model.

## Choose a model for each task

In **Settings → Ollama (Local) → Model Selection (per task)**, set:

| Task | Used for |
| --- | --- |
| **Chat Model** | Normal chat, and the default model for other text tasks |
| **Chat Naming Model** | Naming chats and branches |
| **Chart Generation Model** | Producing charts |
| **Web Content Validation Model** | Validating web content |
| **Web Content Summarization Model** | Summarizing web content |

Choose an installed model by name, or use **Auto – choose a compatible installed model**. The four non-chat tasks also offer **Use chat model** to inherit the chat selection. **Custom model ID...** is an advanced fallback for an alias or ID missing from the inventory; it does not download that model and will only work if the Ollama runtime can serve it. An ordinary setup should use the discovered model list rather than an old scan-and-type workflow. Ollama does not have a per-task image-generation model setting.

If **Ollama (Local)** is not the active mode, choose **Use This Provider** at the top of its Settings page. **Reasoning Level** offers **Off**, **Low**, **Medium** and **High**, subject to model support.

## Switch models in the composer and Agent

The **model chip** in the chat composer opens **Choose a model**, a searchable picker of installed local models and configured cloud models. Search by provider or model name; entries display their model label, a **Local** marker where appropriate, capability badges (**tools**, **vision**, **embeddings**, **thinking**) and size if known. Select a model for the next send. The picker disables a confirmed embedding-only model for chat and explains why; unknown capabilities alone do not disable an entry. **Set up a local model** opens the Ollama Settings page.

When **Agent** is enabled in the main composer, a task uses the selected composer model. On an existing Agent card, use **Choose agent model** to open the same model picker for the next Agent task. This changes the shared current model selection rather than installing a model or silently changing a running task. When using a model without native tools, the workspace Agent may use its constrained JSON tool protocol if the model/runtime supports it; see [Workspace Agent](WORKSPACE_AGENT.md) and [JSON tool protocol](WORKSPACE_JSON_TOOLS.md). A separately pinned branch or node can have its own model route; choosing a model in the composer does not rewrite historical replies.

## Configure an Ollama context window per model

On an Ollama model card, choose **Context window (num_ctx)**. Graphlink stores the value **per model**, and uses the served window for request options and context budgeting. **8k (8,192 tokens) is the default**, not a fixed Ollama limit.

Available choices are **4k, 8k, 16k, 32k, 64k and 128k**, limited by that model's **server-verified trained context length**. If the server cannot verify the length, choices above 8k are not available; a disk scan alone cannot establish a larger safe limit. Higher trained capacity does not mean you should always allocate it: a larger KV cache can consume substantially more memory.

When a card is inspected, Graphlink estimates **model weights + KV cache** for the selected context window and compares that estimate with **available system RAM**. The estimate uses the model size and attention metadata when available (and a conservative KV fallback otherwise). It is **not** a guarantee of actual memory usage: GPU VRAM and other Ollama allocations are not included in the RAM comparison. If the estimate exceeds available system RAM, a warning says the window may run out of memory. Reduce the window or choose a smaller model if you encounter memory pressure. See [Context Engine](CONTEXT_ENGINE.md) for how a served window affects chat prompts.

## Run a model Check

Select **Check** on a model card to run a short, cancellable diagnostic. It asks the model to generate a brief reply and requests a harmless **native function call** using an actual Ollama tool definition; no tool is executed. For supported reasoning models such as Qwen3, the tool probe uses the runtime's Quick/no-thinking setting and retries once if the response contains reasoning but no call. The card reports **load time**, **generation speed (tokens/s)** and whether a **tool call was observed**; unavailable counters are reported as unavailable rather than invented. If neither attempt returns a call, the result is **Inconclusive**, not proof that tools are unsupported. **Cancel** stops an in-progress check.

**Check is a report only.** It may load or run the model, but it does **not** change saved settings or model assignments, and **never executes** the requested tool call. It is a small probe, not a guarantee of performance on your own workloads. An inconclusive native-tool check does not override Ollama's advertised tools capability or rule out the Agent's JSON-tool fallback.

## Understand capability warnings

- If you attach an image in the composer and the selected model is **confirmed not to support vision**, Graphlink warns you before sending. Pick a vision-capable model.
- With **Agent** mode enabled, the main composer warns **before the first task is sent** if the selected model is confirmed not to support native tool calling. Idle Agent cards retain this warning for follow-up tasks. Graphlink may suggest a **server-verified installed Ollama model** with tools whose estimated weights plus context-window KV cache fit **available system RAM**. This is an estimate, not a guarantee of actual memory usage; if the fit cannot be established, it suggests no particular model. Agent's constrained JSON fallback may still work where supported.
- **Unknown/unavailable** metadata does not trigger those unsupported-capability warnings. If you are unsure, start Ollama, refresh the inventory, and use **Check** to probe native tool calling.

Badges and warnings describe reported or observed support, not a promise that a particular prompt, image format or tool schema will succeed.

## Use a llama.cpp GGUF model instead

1. Install `llama-cpp-python` **in the same Python environment that runs Graphlink** (for example, `python -m pip install llama-cpp-python`). Obtain a compatible **.gguf** model file separately; Ollama and its server are not required for this provider.
2. Open **Settings → Llama.cpp (Local)**. **System Scan** searches for GGUF files and **Scan Folder...** searches a chosen directory. Select a **Scanned Chat Model**, or use **Browse...** under **Chat Model File** to choose a specific `.gguf`.
3. Optionally select a **Scanned Naming Model** or **Chat Naming File**. Leaving the naming path blank reuses the chat model.
4. Adjust **Chat Format Override** only if necessary (blank uses GGUF metadata), **Context Window**, **GPU Layers**, **CPU Threads** and **Reasoning Level** for your environment. These llama.cpp settings are separate from Ollama's per-model `num_ctx` choices.
5. Select **Save Settings**, then **Use This Provider** if another mode is active. Save first: browsing/scanning stages a path, and switching modes uses the last saved llama.cpp configuration.

Only GGUF paths actually configured as chat/naming models are available to run in the model/comparison pickers; merely scanning a file does not configure it.

## Local does not always mean offline

Once a local model and runtime are installed, ordinary local inference does not require a hosted model API key. **Downloading** models needs a connection, and web research, approved Agent network tools, cloud model choices and other integrations may still communicate outside your machine. Keep Ollama pointed at a local daemon and avoid cloud-backed Ollama aliases if you require on-device inference. See [Local data and privacy](../README.md#local-data-and-privacy).
