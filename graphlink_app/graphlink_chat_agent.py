"""Qt-free chat-agent core (Qt-removal plan R4.2 prerequisite).

`resolve_branch_system_prompt`/`ChatWorker`/`ChatAgent` moved out of
graphlink_agents_core.py: that module's unconditional
`from PySide6.QtCore import QThread, Signal, QPointF` (needed only by its
`*WorkerThread` classes) meant importing anything from it - including these
three Qt-free symbols - pulled PySide6 into the process. That made them
unimportable from backend/ despite containing zero Qt code themselves,
exactly the R4.1 problem R4.1 itself didn't reach (it only split
graphlink_config.py).

graphlink_agents_core.py re-exports all three unchanged for its own
ChatWorkerThread (which calls resolve_branch_system_prompt) and every
legacy Qt call site - see its own import line.

This file must stay Qt-free forever - it exists to be importable from
backend/, which test_no_qt_anywhere.py holds to zero tolerance.
"""

import json

import graphlink_task_config as config
import api_provider
from graphlink_token_estimator import TokenEstimator
from graphlink_memory import trim_history


def resolve_branch_system_prompt(current_node, default_system_prompt):
    """Resolve the effective system prompt for a chat branch.

    Walks up to the branch root and, if a system-prompt note is attached there, uses its
    content instead of the default. This reads live QGraphicsScene objects
    (``parent_node``/``scene()``/``system_prompt_connections``/``prompt_note.content``),
    so it MUST run on the GUI thread - QGraphicsScene is not thread-safe, and doing this
    walk from a worker thread races node deletion / scene.clear() and can crash or read
    torn state. Callers resolve it here on the UI thread and hand the resulting string
    to the worker, which never touches the scene.

    Returns the final system prompt string (the default if nothing overrides it).
    """
    final_system_prompt = default_system_prompt
    if not (default_system_prompt or "").strip():
        return default_system_prompt
    if not current_node:
        return final_system_prompt

    root_node = current_node
    while root_node.parent_node:
        root_node = root_node.parent_node

    if root_node.scene():
        for conn in root_node.scene().system_prompt_connections:
            if conn.end_node == root_node:
                prompt_note = conn.start_node
                if prompt_note.content:
                    final_system_prompt = prompt_note.content
                break
    return final_system_prompt


class ChatWorker:
    """
    A stateless worker class that encapsulates the logic for a single chat API call.
    It determines the correct system prompt to use based on the conversation context.
    """
    def __init__(self, system_prompt):
        """
        Initializes the ChatWorker.

        Args:
            system_prompt (str): The default system prompt to use if no custom one is found.
        """
        self.system_prompt = system_prompt
        self.token_estimator = TokenEstimator()
        self.MAX_TOKENS = 8000

    def run(self, conversation_history, current_node, cancellation_event=None, resolved_system_prompt=None):
        """
        Executes the chat logic for a single turn.

        Args:
            conversation_history (list): The list of messages in the conversation.
            current_node (QGraphicsItem): The current node context. Only used to resolve the
                branch system prompt when `resolved_system_prompt` is not supplied.
            resolved_system_prompt (str, optional): The branch system prompt already
                resolved on the GUI thread. When provided, the scene is NOT walked here -
                this is how the worker-thread path avoids touching QGraphicsScene (#20).

        Returns:
            str: The AI-generated response text.

        Raises:
            Exception: Propagates exceptions from the API provider.
        """
        if resolved_system_prompt is not None:
            final_system_prompt = resolved_system_prompt
        else:
            # Legacy/direct path (no pre-resolution): safe only on the GUI thread.
            final_system_prompt = resolve_branch_system_prompt(current_node, self.system_prompt)
        use_system_prompt = bool((final_system_prompt or "").strip())

        try:
            sys_tokens = 0
            messages = []
            if use_system_prompt:
                system_msg = {'role': 'system', 'content': final_system_prompt}
                sys_tokens = self.token_estimator.count_tokens(json.dumps(system_msg))
                messages.append(system_msg)
            trimmed_history, _ = trim_history(
                conversation_history,
                self.token_estimator,
                max_tokens=self.MAX_TOKENS,
                system_prompt_estimate=sys_tokens,
            )

            messages.extend(trimmed_history)

            response = api_provider.chat(
                task=config.TASK_CHAT,
                messages=messages,
                cancellation_event=cancellation_event,
            )
            ai_message = response['message']['content']
            return ai_message
        except Exception as e:
            print(f"  [LOG-CHATWORKER] API call failed: {e}")
            raise e


class ChatAgent:
    """
    The primary agent for handling general-purpose chat conversations.
    This agent is stateless; it relies on the conversation history passed to it for context.
    """
    def __init__(self, name, persona):
        """
        Initializes the ChatAgent.

        Args:
            name (str): The name of the AI assistant.
            persona (str): The detailed system prompt defining the AI's behavior and knowledge.
        """
        self.name = name or "AI Assistant"
        self.persona = persona or "(default persona)"
        self.system_prompt = f"You are {self.name}. {self.persona}"

    def get_response(self, conversation_history, current_node, cancellation_event=None, resolved_system_prompt=None):
        """
        Gets an AI response for a given conversation history.

        Args:
            conversation_history (list): The list of messages in the conversation.
            current_node (QGraphicsItem): The current node context.
            resolved_system_prompt (str, optional): Branch system prompt already resolved
                on the GUI thread; passed straight through so the worker never walks the
                scene itself (#20).

        Returns:
            str: The AI-generated response text.
        """
        # This agent is stateless. It does not store conversation_history.
        # It creates a temporary ChatWorker to handle the API call.
        chat_worker = ChatWorker(self.system_prompt)
        ai_response = chat_worker.run(
            conversation_history,
            current_node,
            cancellation_event=cancellation_event,
            resolved_system_prompt=resolved_system_prompt,
        )
        return ai_response
