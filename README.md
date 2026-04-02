<div align="center">

# Graphlink

![GitHub stars](https://img.shields.io/github/stars/dovvnloading/Graphlink?style=social) 
![GitHub forks](https://img.shields.io/github/forks/dovvnloading/Graphlink?style=social) 
![License](https://img.shields.io/badge/License-MIT-green) 
![Python](https://img.shields.io/badge/Python-3.8%2B-blue) 
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
- Specialist plugins for validation, delivery, workflow planning, drafting, code execution, and repository-aware changes.
- Support for both local Ollama models and API-backed providers.
- Local-first persistence of conversations, notes, pins, and graph layout.
- Export helpers for text, Markdown, HTML, Python, DOCX, and PDF outputs.

### Highlights
- **Visual branching workspace**: Build multiple parallel thought paths, experiments, and delivery tracks in one view.
- **Plugin-driven workflow**: Add specialized nodes such as Workflow Architect, Branch Lens, Quality Gate, Code Review Agent, Gitlink, Py-Coder, and Execution Sandbox.
- **Provider flexibility**: Run locally with Ollama, or switch to API Endpoint mode for OpenAI-compatible providers and Google Gemini.
- **Review and delivery tooling**: Compare branches, run production-readiness checks, review code with a deterministic rubric, and stage repo-aware file changes before writing them.
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
- **Graphlink-Reasoning**: Multi-step reasoning workflow for harder tasks.
- **Graphlink-Web**: Web-enabled research node for real-time retrieval and summarization.

### Validation & Delivery
- **Branch Lens**: Compare two branches and surface differences in logic, intent, or implementation direction.
- **Quality Gate**: Run a production-readiness review, score the branch, and recommend follow-up remediation nodes.
- **Code Review Agent**: Review a local file or GitHub file with a deterministic weighted rubric and structured findings.

### Build & Execution

- **Gitlink**: Load a repository context, prepare file-level changes, preview them, and write only after explicit approval.
- **Py-Coder**: Run Python snippets and get AI-assisted coding analysis.
- **Execution Sandbox**: Execute Python in an isolated virtual environment with per-node dependency control.
- **HTML Renderer**: Render generated HTML from a parent branch directly inside the app.

### Workflow & Drafting
- **Workflow Architect**: Generate an execution plan and seed the best next specialist nodes.
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
*Note: The `requirements.txt` includes libraries such as `PySide6`, `ollama`, `openai`, `google-generativeai`, `requests`, `qtawesome`, `Markdown`, `reportlab`, `python-docx`, `pypdf`, `Pillow`, `tiktoken`, `pygments`, `beautifulsoup4`, `ddgs`, `matplotlib`, and `pyspellchecker`.*

**4. Choose a Model Strategy**
You can run Graphlink in either of these modes:
- **Ollama (Local)**: Best for local-first usage with self-hosted models.
- **API Endpoint**: Best when using OpenAI-compatible APIs or Google Gemini.

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
Local mode is the default path. The app ships with Ollama task defaults in `graphite_app/graphite_config.py`.
Current defaults include:
- Chat/title/web tasks: `qwen3:8b`
- Chart/code-capable task: `deepseek-coder:6.7b`

Recommended local setup:
```powershell
ollama serve
ollama pull qwen3:8b
ollama pull deepseek-coder:6.7b
```
Then start Graphlink and keep the mode set to **Ollama (Local)**.

**API Endpoint Mode**
The app supports **OpenAI-Compatible** and **Google Gemini** endpoints.
The API settings UI supports per-task model selection for:
- title generation
- main chat / explain / takeaway
- chart generation
- web validation
- web summarization

### Common Environment Variables
Some settings paths and dialogs read these values:
- `GRAPHITE_API_PROVIDER`
- `GRAPHITE_API_BASE`
- `GRAPHITE_OPENAI_API_KEY`
- `GRAPHITE_GEMINI_API_KEY`
- `GRAPHITE_API_KEY`
- `GEMINI_API_KEY`

In practice, the in-app settings flow is the main configuration surface, but these environment variables are still relevant during development.

### GitHub Integration
GitHub-backed features are used by **Code Review Agent** and **Gitlink**.
To use private repositories, save a GitHub access token in the app settings.

---

## Usage Guide

### How to Use the App

**Start a New Graph**
Launch the app, create or load a session, and begin with a chat node or starter prompt.

**Build Branches**
Select nodes and add plugins from the plugin picker or controls. Each new node can become the start of a more specialized path such as research, code generation, drafting, validation, or execution.

**Validate Work**
Use validation-oriented nodes when a branch is moving from exploration toward delivery:
- **Branch Lens** to compare alternative directions
- **Quality Gate** to judge readiness
- **Code Review Agent** for file-level review

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
- **Local model runtime**: Ollama
- **API providers**: OpenAI-compatible endpoints, Google Gemini
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
4. Providers route requests to Ollama or API-backed services.
5. The session manager serializes the graph into SQLite-backed storage.
6. Reloading reconstructs the graph, branch history, notes, and plugin state.

### Repository Layout
```text
.
|-- assets/
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
|   `-- graphite_ui_dialogs/
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
Several top-level modules such as `graphite_plugin_code_review.py`, `graphite_plugin_workflow.py`, and similar files are lightweight compatibility wrappers. The real implementations live in the package directories:
- `graphite_app/graphite_plugins/`
- `graphite_app/graphite_nodes/`
- `graphite_app/graphite_canvas/`
- `graphite_app/graphite_ui_dialogs/`

If you are making code changes, prefer editing the concrete implementation modules first. Do not assume a top-level wrapper file is the authoritative implementation. 

### Visual Studio Support
The repository includes `graphite_app.sln` and `graphite_app/graphite_app.pyproj`. This makes the project easy to work with on Windows, but you can also use a standard Python virtual environment and run from the terminal.

### No Formal Test Suite Yet
This repository currently does not ship with a full automated test suite. The included GitHub Actions workflow performs a Python compile smoke check to catch syntax and merge-level breakage early.

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
- A broader automated test suite is still needed.

---

## Community & Security

### Contributing
Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) for setup, workflow, and pull request guidance.

### Security
If you discover a security-sensitive issue, please avoid posting exploit details publicly before the maintainer has a chance to review and patch it. See [SECURITY.md](SECURITY.md) for more details.
