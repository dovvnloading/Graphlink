"""
Facade module for Graphite AI Agents and Workers.

This module re-exports all agents, enums, and background workers from their 
respective segmented modules to maintain backward compatibility with the rest 
of the application (e.g., graphite_window.py, graphite_settings_dialogs.py).
"""

# --- Core Chat & Text Processing Agents ---
from graphite_agents_core import (
    ChatWorkerThread,
    ChatWorker,
    ChatAgent,
    ExplainerAgent,
    ExplainerWorkerThread,
    KeyTakeawayAgent,
    KeyTakeawayWorkerThread,
    GroupSummaryAgent,
    GroupSummaryWorkerThread
)

# --- Tool-based Agents (Charts, Images, Models) ---
from graphite_agents_tools import (
    ChartDataAgent,
    ChartWorkerThread,
    ImageGenerationAgent,
    ImageGenerationWorkerThread,
    ModelPullWorkerThread
)

# --- Py-Coder Code Execution Agents ---
from graphite_agents_pycoder import (
    PyCoderStage,
    PyCoderStatus,
    CodeExecutionWorker,
    PyCoderExecutionAgent,
    PyCoderRepairAgent,
    PyCoderAnalysisAgent,
    PyCoderExecutionWorker,
    PyCoderAgentWorker
)

# --- Isolated Sandbox Execution Agents ---
from graphite_agents_code_sandbox import (
    SandboxStage,
    CodeSandboxExecutionWorker
)

# --- Web Search Agents ---
from graphite_agents_web import (
    DUCKDUCKGO_SEARCH_AVAILABLE,
    REQUESTS_AVAILABLE,
    BEAUTIFULSOUP_AVAILABLE,
    WebSearchAgent,
    WebWorkerThread
)

# --- Multi-Step Reasoning Agents ---
from graphite_agents_reasoning import (
    ReasoningAgent,
    ReasoningWorkerThread
)
