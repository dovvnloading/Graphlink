<div align="center">
 
# Graphlink

![GitHub stars](https://img.shields.io/github/stars/dovvnloading/Graphlink?style=social) 
![GitHub forks](https://img.shields.io/github/forks/dovvnloading/Graphlink?style=social) 
![License](https://img.shields.io/badge/License-MIT-green) 
![Python](https://img.shields.io/badge/Python-3.10%2B-blue) 
![PySide6](https://img.shields.io/badge/PySide6-Qt%20for%20Python-darkgreen) 
![Qt](https://img.shields.io/badge/Qt-Framework-41CD52) 
![Ollama](https://img.shields.io/badge/Ollama-Local%20Models-black) 
![OpenAI](https://img.shields.io/badge/OpenAI-Compatible-412991) 
![Local First](https://img.shields.io/badge/Local--First-AI%20Workspace-orange)

<img width="1920" height="1080" alt="graphite-render-1920x1080-1774826472139" src="https://github.com/user-attachments/assets/3fda5311-1d05-49c9-8654-04649f902b8a" />

</div>

---

**Graphlink** is a desktop, graph-based AI workspace designed for structured reasoning, experimentation, and complex problem solving. Built with **Python** and **PySide6**, it replaces the traditional linear chat interface with a **visual canvas of connected nodes**, allowing users to explore ideas, run tools, and orchestrate AI-assisted workflows in parallel.

Instead of compressing every interaction into a single conversational timeline, Graphlink enables users to build **branching reasoning graphs**. Conversations, code generation, web retrieval, analysis, drafting, validation, and execution can all exist as separate nodes connected within a larger workspace. Each branch can follow its own line of inquiry, use different tools or models, and maintain its own contextual boundaries.

Graphlink represents the **second generation of the original Graphite project**. What began as a prototype visual interface for AI conversations has evolved into a more complete reasoning environment with expanded architecture, plugin tooling, agent workflows, and a significantly improved user interface. As part of this evolution, the project has been **renamed from Graphite to Graphlink** to avoid confusion with other unrelated software using the Graphite name and to better reflect the system’s focus on connected reasoning environments.

You may still see **Graphite** referenced throughout parts of the codebase, repository structure, and module names. This is expected during the transition period while the project moves fully toward the Graphlink identity.

At its core, Graphlink is designed around a simple idea: complex work rarely happens in a straight line. By giving users a visual environment where ideas, tools, and AI reasoning can branch, interact, and evolve, Graphlink turns AI from a conversational assistant into a **workspace for thinking and building**.

---

## Table of Contents

- [Overview & Features](#overview--features)
- [Interface Gallery](#interface-gallery)
- [Plugin Ecosystem](#plugin-ecosystem)
- [Getting Started](#getting-started)
- [Configuration & Setup](#configuration--setup)
- [Usage Guide](#usage-guide)
- [System Architecture](#system-architecture)
- [Development Notes](#development-notes)
- [Troubleshooting & Limitations](#troubleshooting--limitations)
- [Community & Security](#community--security)

---

## Overview & Features

### What It Does
Graphlink combines a node canvas, local persistence, multiple model backends, and a plugin system so a single branch can evolve from an idea into a reviewed, validated, and exportable result.

Core capabilities include:
- Branching conversations on a visual canvas instead of a single threaded prompt log.
- Built-in node types for chat, code, documents, images, and thinking/reasoning outputs.
- Specialist plugins for research, drafting, code execution, and repository-aware changes.
- Support for both local providers (Ollama and llama.cpp via `llama-cpp-python`) and API-backed providers.
- Local-first persistence of conversations, notes, pins, and graph layout.
- Export helpers for text, Markdown, HTML, Python, DOCX, and PDF outputs.

### Highlights
- **Visual branching workspace**: Build multiple parallel thought paths, experiments, and delivery tracks in one view.
- **Plugin-driven workflow**: Add specialized nodes such as Gitlink, Py-Coder, Execution Sandbox, and Artifact / Drafter.
- **Provider flexibility**: Run locally with Ollama or direct GGUF loading through llama.cpp, or switch to API Endpoint mode for OpenAI-compatible providers, Anthropic Claude, or Google Gemini.
- **Repository-aware delivery**: Load a repo into structured context with Gitlink, preview file-level changes, and only write them after explicit approval.
- **Structured persistence**: Sessions are stored locally in SQLite with notes and navigation pins kept separately for efficient reloads.
- **Windows-friendly development**: The repository includes a Visual Studio solution and Python project for local editing on Windows.

### Core Node Types
The app also includes primary non-plugin node types that form the main graph surface:
- Chat nodes
- Code nodes
- Document nodes
- Image nodes
- Thinking nodes
- Notes
- Frames and containers
- Navigation pins
- Charts

---

## Interface Gallery

<div align="center">
  <img width="1920" height="1080" alt="graphite-render-1920x1080-1774826552381" src="https://github.com/user-attachments/assets/dc477feb-a8bf-4f0d-8914-42371329e725" style="margin-bottom: 20px;" />
  <img width="1920" height="1080" alt="graphite-render-1920x1080-1774826487402" src="https://github.com/user-attachments/assets/9bfe2cde-70e5-433a-b86d-5bb99105d91f" />
<img width="1920" height="1080" alt="graphite-render-1920x1080-1775163980518" src="https://github.com/user-attachments/assets/93cb0452-18c3-4419-9857-b816a90b7350" />
  
</div>

---

## Plugin Ecosystem

### Branch Foundations
- **System Prompt**: Attach a branch-specific system prompt to shape downstream model behavior.
- **Conversation Node**: Create a self-contained linear conversation inside a node.

### Reasoning & Research
- **Graphlink-Web**: Web-enabled research node for real-time retrieval and summarization.

### Build & Execution

- **Gitlink**: Load a repository context, prepare file-level changes, preview them, and write only after explicit approval.
- **Py-Coder**: Run Python snippets and get AI-assisted coding analysis.
- **Execution Sandbox**: Execute Python in an isolated virtual environment with per-node dependency control.
- **HTML Renderer**: Render generated HTML from a parent branch directly inside the app.

### Workflow & Drafting
- **Artifact / Drafter**: Draft and refine long-form Markdown artifacts in a split-pane writing surface.

---

## Getting Started

### Requirements
- **Runtime**: Python 3.10 or newer. Windows is the primary development target today.
- **Internet Access**: Optional, but required for API Endpoint mode, GitHub-backed plugin flows, and web research features.

### Quick Start

**1. Clone the Repository**
```powershell
git clone <your-repo-url>
cd graphite_app
```

**2. Create and Activate a Virtual Environment**
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

**3. Install Dependencies**
```powershell
pip install --upgrade pip
pip install -r requirements.txt
```
*Note: `requirements.txt` includes `PySide6`, `ollama`, `openai`, `anthropic`, `google-generativeai`, `llama-cpp-python`, `requests`, `qtawesome`, `Markdown`, `reportlab`, `python-docx`, `pypdf`, `Pillow`, `mutagen`, `tiktoken`, `pygments`, `beautifulsoup4`, `ddgs`, `matplotlib`, and `pyspellchecker` - a single install covers Ollama, Llama.cpp (GGUF), and every API Endpoint provider, no separate install step needed.*

**4. Choose a Model Strategy**
You can run Graphlink in either of these modes:
- **Ollama (Local)**: Best for local-first usage with Ollama-managed models.
- **Llama.cpp (Local)**: Best when you want direct GGUF file loading through `llama-cpp-python` with runtime controls.
- **API Endpoint**: Best when using OpenAI-compatible APIs, Anthropic Claude, or Google Gemini.

**5. Launch the App**
From the repository root, move into the app directory and run:
```powershell
cd graphite_app
python graphite_app.py
```
If you prefer Visual Studio, open `graphite_app.sln` or `graphite_app/graphite_app.pyproj`.

### First-Run Behavior
On first launch, Graphlink creates a local application directory at `~/.graphlink`.
This directory stores:
- `chats.db` for graph sessions, notes, and pins.
- `session.dat` for local settings and saved credentials.

The welcome screen provides recent projects, starter prompts, and a quick path into a new graph session.

---

## Configuration & Setup

### Model and Provider Setup

**Ollama (Local)**
Local mode is the default path. Every per-task model is independently configurable in Settings and persists across launches - nothing is permanently hardcoded. The out-of-the-box defaults (used until you save your own) are:
- Chat and Chat Naming: `qwen3:8b`
- Chart Generation: `deepseek-coder:6.7b`
- Web Content Validation and Summarization: whatever you've set as your chat model

Recommended local setup for the defaults:
```powershell
ollama serve
ollama pull qwen3:8b
ollama pull deepseek-coder:6.7b
```
Then start Graphlink and keep the mode set to **Ollama (Local)**. If you'd rather use different models, pull those instead and set them per-task in Settings > Ollama - you don't need `qwen3:8b`/`deepseek-coder:6.7b` specifically.

**Llama.cpp (Local)**
This mode uses **direct GGUF loading** through `llama-cpp-python` instead of a local model server.
Use the Settings panel to configure:
- Chat GGUF file (required)
- Optional chat naming GGUF file (falls back to chat model when empty)
- Reasoning mode (Thinking or Quick)
- Runtime controls: `n_ctx`, `n_gpu_layers`, `n_threads`, optional `chat_format` override

Model discovery supports:
- **System Scan** of common folders (`LLAMA_CPP_MODELS`, `~/models`, `~/llama.cpp`, Downloads/Documents/Desktop, LM Studio cache, etc.)
- **Scan Folder** for a custom directory
- GGUF files only (`.gguf`)

Llama.cpp compatibility notes in Graphlink:
- Text chat and title generation are supported.
- Image/audio attachments are **not** supported in this mode.
- Image generation remains API Endpoint-only.
- Ollama manifest/blob storage is not valid as a llama.cpp GGUF path.

**API Endpoint Mode**
The app supports **OpenAI-Compatible**, **Anthropic Claude**, and **Google Gemini** endpoints.
- Image generation is currently OpenAI-Compatible only; Anthropic Claude does not support it in Graphlink yet.
- Anthropic Claude accepts image attachments but not audio attachments (switch to Gemini or Ollama for audio).

The API settings UI supports per-task model selection for:
- title generation
- main chat / explain / takeaway
- chart generation
- web validation
- web summarization

### Common Environment Variables
The app reads these as fallbacks when no key is saved in Settings, or for model-discovery paths:
- `GRAPHITE_ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEY` - Anthropic Claude key
- `GRAPHITE_GEMINI_API_KEY` / `GEMINI_API_KEY` - Google Gemini key
- `LLAMA_CPP_MODELS` - root folder scanned for GGUF files in Llama.cpp mode
- `OLLAMA_MODELS` - override for Ollama's model storage root during local model discovery

OpenAI-Compatible mode does not currently read an API key from the environment - its key is settings-only. In practice, the in-app settings flow is the main configuration surface; these environment variables mostly matter during development.

### GitHub Integration
GitHub-backed features are used by **Gitlink**.
To use private repositories, save a GitHub access token in the app settings.

---

## Usage Guide

### How to Use the App

**Start a New Graph**
Launch the app, create or load a session, and begin with a chat node or starter prompt.

**Build Branches**
Select nodes and add plugins from the plugin picker or controls. Each new node can become the start of a more specialized path such as research, code generation, drafting, or execution.

**Execute or Draft**
Use build-oriented nodes when you want to move from planning into artifacts:
- **Gitlink** for repo-aware change proposals
- **Py-Coder** for direct Python execution
- **Execution Sandbox** for isolated dependency-aware runs
- **Artifact / Drafter** for Markdown documents

**Export Content**
Export helpers support: `.txt`, `.py`, `.md`, `.html`, `.docx`, `.pdf`.

**File Ingestion**
The file handling layer supports reading: `.txt`, `.md`, `.py`, `.json`, `.html`, `.css`, `.js`, `.csv`, `.xml`, `.pdf`, `.docx`.

---

## System Architecture

### Technology Stack
- **Language**: Python
- **Desktop UI**: PySide6 / Qt
- **Local model runtimes**: Ollama, llama.cpp via `llama-cpp-python`
- **API providers**: OpenAI-compatible endpoints, Anthropic Claude, Google Gemini
- **HTTP / integrations**: `requests`
- **Export / file support**: Markdown, ReportLab, `python-docx`, `pypdf`, Pillow
- **Persistence**: SQLite + JSON payload serialization
- **Icons**: `qtawesome`

### Persistence Model
Graphlink uses local SQLite storage and graph serialization instead of a cloud-only session model. Stored locally:
- graph nodes, connections, notes, navigation pins
- branch state, conversation history, plugin state

**Storage Paths**
```text
~/.graphlink/chats.db
~/.graphlink/session.dat
```
**Important Security Note**: The current settings system stores API keys and GitHub tokens locally in `session.dat`. Before distributing packaged builds or using this in a shared environment, review that storage model and decide whether you want to move secrets into a stronger credential storage approach.

### Architecture Overview

**Main Runtime Areas**
- `graphite_app/graphite_app.py`: application boot
- `graphite_app/graphite_window.py`: main shell
- `graphite_app/graphite_window_actions.py`: action dispatch and response flow
- `graphite_app/graphite_window_navigation.py`: shortcuts and command palette behavior
- `graphite_app/graphite_view.py`: viewport interactions
- `graphite_app/graphite_scene.py`: graph ownership and node/connection registries
- `graphite_app/graphite_core.py`: persistence and session serialization
- `graphite_app/graphite_memory.py`: branch memory and transcript shaping
- `graphite_app/api_provider.py`: provider abstraction
- `graphite_app/graphite_plugins/`: plugin implementations

**Runtime Flow**
At a high level, Graphlink works like this:
1. The app boots a Qt application and applies saved settings.
2. The main window owns the graph view, plugin portal, and interaction shell.
3. Nodes and plugins spawn worker threads for AI or execution tasks.
4. Providers route requests to Ollama, llama.cpp local runtime, or API-backed services.
5. The session manager serializes the graph into SQLite-backed storage.
6. Reloading reconstructs the graph, branch history, notes, and plugin state.

### Repository Layout
```text
.
|-- .github/
|-- assets/
|-- doc/
|-- graphite_app.sln
|-- requirements.txt
|-- graphite_app/
|   |-- graphite_app.py
|   |-- graphite_window.py
|   |-- graphite_window_actions.py
|   |-- graphite_window_navigation.py
|   |-- graphite_scene.py
|   |-- graphite_view.py
|   |-- graphite_core.py
|   |-- graphite_memory.py
|   |-- api_provider.py
|   |-- graphite_nodes/
|   |-- graphite_canvas/
|   |-- graphite_plugins/
|   |-- graphite_ui_dialogs/
|   `-- tests/
`-- GRAPHITE_REPO_NAVIGATION.md
```

---

## Development Notes

### Important Structural Note
The app is currently **script-oriented**, not a fully namespaced package-first layout. Many imports assume the working directory is the `graphite_app/` folder itself.
That means the safest terminal launch path is:
```powershell
cd graphite_app
python graphite_app.py
```

### Compatibility Facades
Several top-level modules such as `graphite_plugin_gitlink.py` and `graphite_plugin_artifact.py` are lightweight compatibility wrappers. The real implementations live in the package directories:
- `graphite_app/graphite_plugins/`
- `graphite_app/graphite_nodes/`
- `graphite_app/graphite_canvas/`
- `graphite_app/graphite_ui_dialogs/`

If you are making code changes, prefer editing the concrete implementation modules first. Do not assume a top-level wrapper file is the authoritative implementation. 

### Visual Studio Support
The repository includes `graphite_app.sln` and `graphite_app/graphite_app.pyproj`. This makes the project easy to work with on Windows, but you can also use a standard Python virtual environment and run from the terminal.

### Test Suite
`graphite_app/tests/` has a `pytest` suite (148 tests as of this writing) covering plugin registration, scene/session serialization, path-safety and JSON-parsing helpers, and Qt node behavior headlessly. Run it from `graphite_app/`:
```powershell
pytest
```
The included GitHub Actions workflow additionally performs a Python compile smoke check to catch syntax and merge-level breakage early.

### GitHub Files Included
This repository now includes GitHub-facing project files for a cleaner public share:
- `README.md`, `.gitignore`
- `CONTRIBUTING.md`, `SECURITY.md`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/pull_request_template.md`
- `.github/workflows/python-smoke.yml`

---

## Troubleshooting & Limitations

### Troubleshooting

**The app does not start**
- Confirm you installed dependencies from `requirements.txt`.
- Confirm you are launching from the `graphite_app/` directory.
- Confirm your Python version is 3.10 or newer.

**Ollama features fail**
- Make sure Ollama is installed and running.
- Pull the required models before launching the app.
- Confirm the selected model exists locally.

**Llama.cpp features fail**
- Install `llama-cpp-python` in the same environment (`pip install llama-cpp-python`).
- Confirm the configured model path points to an existing `.gguf` file.
- If the model does not respond correctly, try setting a `chat_format` override or reducing runtime settings.
- For image/audio attachments, switch to Ollama or API Endpoint mode.

**API mode fails**
- Verify your API key is present.
- Verify your base URL is correct for OpenAI-compatible mode.
- Verify the selected models exist on the configured endpoint.

**GitHub-backed plugins fail**
- Save a valid GitHub token in settings.
- Confirm the token can access the target repository.
- Confirm the target repository path and branch exist.

**Export/import features fail**
- Reinstall dependencies from `requirements.txt`.
- Verify the destination path is writable.
- Confirm the file type is one of the supported import/export formats.

### Current Limitations
- The codebase is still partly organized around compatibility wrappers.
- The app is Windows-first today, even though much of the Python code is portable.
- Some settings flows still mix environment-based and persisted configuration behavior.
- Secrets are stored locally in a plain application state file.
- Test coverage is headless (Qt widgets, JSON/path-safety helpers, serialization) rather than end-to-end UI coverage.

---

## Community & Security

### Contributing
Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) for setup, workflow, and pull request guidance.

### Security
If you discover a security-sensitive issue, please avoid posting exploit details publicly before the maintainer has a chance to review and patch it. See [SECURITY.md](SECURITY.md) for more details.
