"""
Facade module for Graphlink AI Agents and Workers.

This module re-exports all agents, enums, and background workers from their
respective segmented modules to maintain backward compatibility with the rest
of the application (e.g., graphlink_window.py, graphlink_settings_bridge.py).
"""

# --- Core Chat & Text Processing Agents ---
from graphlink_agents_core import (
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
from graphlink_agents_tools import (
    ChartDataAgent,
    ChartWorkerThread,
    ImageGenerationAgent,
    ImageGenerationWorkerThread,
    ModelPullWorkerThread
)

# --- Py-Coder Code Execution Agents ---
from graphlink_agents_pycoder import (
    PyCoderStage,
    PyCoderStatus,
    CodeExecutionWorker,
    PyCoderExecutionAgent,
    PyCoderRepairAgent,
    PyCoderAnalysisAgent,
    PyCoderExecutionWorker,
    PyCoderAgentWorker,
    PyCoderReplManager
)

# --- Isolated Sandbox Execution Agents ---
from graphlink_agents_code_sandbox import (
    SandboxStage,
    CodeSandboxExecutionWorker
)

# --- Web Search Agents ---
from graphlink_agents_web import (
    DUCKDUCKGO_SEARCH_AVAILABLE,
    REQUESTS_AVAILABLE,
    BEAUTIFULSOUP_AVAILABLE,
    WebSearchAgent,
    WebWorkerThread
)
