import re
import threading
import time
from PySide6.QtCore import QThread, Signal, QPointF
# Qt-removal plan R4.2: resolve_branch_system_prompt/ChatWorker/ChatAgent
# moved to the Qt-free graphlink_chat_agent so backend/ can call the real
# chat layer - this module's own PySide6 import (needed only by the
# *WorkerThread classes below) would otherwise pull Qt into any importer.
# Still needed here directly: ExplainerAgent/KeyTakeawayAgent/GroupSummaryAgent
# (below) call api_provider.chat(task=config.TASK_CHAT, ...) themselves.
import graphlink_task_config as config
import api_provider
from graphlink_chat_agent import resolve_branch_system_prompt, ChatWorker, ChatAgent


class ChatWorkerThread(QThread):
    """
    A QThread worker for handling standard chat conversations in the background.

    This thread takes a ChatAgent and the current conversation context, runs the
    agent to get a response, and emits a 'finished' signal with the new message
    or an 'error' signal if something goes wrong.
    """
    finished = Signal(dict) # Emits the new message dictionary on success.
    error = Signal(str)     # Emits an error message string on failure.
    status = Signal(str)    # Emits progress / stall notices.
    cancelled = Signal()    # Emits when the user cancels the request.
    
    def __init__(self, agent, conversation_history, current_node):
        """
        Initializes the ChatWorkerThread.

        Args:
            agent (ChatAgent): The AI agent instance to use for generating the response.
            conversation_history (list): A list of message dictionaries representing the
                                         conversation up to this point.
            current_node (QGraphicsItem): The node from which the new message is branching,
                                          used to determine context like system prompts.
        """
        super().__init__()
        self.agent = agent
        self.conversation_history = conversation_history if isinstance(conversation_history, list) else []
        self.current_node = current_node
        # __init__ runs on the GUI thread (the caller's thread), so resolve any
        # branch-scoped system prompt from the live scene HERE, before the worker thread
        # starts. run() must never walk the scene itself (#20).
        default_prompt = getattr(agent, "system_prompt", "") if agent else ""
        self.resolved_system_prompt = resolve_branch_system_prompt(current_node, default_prompt)
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()
        self.requestInterruption()
        
    def run(self):
        """
        The main execution method for the thread. This is called when the thread starts.
        It runs the agent and emits the result.
        """
        result_holder = {}
        error_holder = {}

        def _invoke():
            try:
                result_holder["response"] = self.agent.get_response(
                    self.conversation_history,
                    self.current_node,
                    cancellation_event=self._cancel_event,
                    resolved_system_prompt=self.resolved_system_prompt,
                )
            except Exception as exc:
                error_holder["error"] = exc

        worker = threading.Thread(target=_invoke, daemon=True)
        worker.start()

        warning_seconds, timeout_seconds = self._watchdog_limits()
        warning_sent = False
        started_at = time.monotonic()

        while worker.is_alive():
            worker.join(0.25)
            if self._cancel_event.is_set() or self.isInterruptionRequested():
                self.cancelled.emit()
                return
            elapsed = time.monotonic() - started_at

            if not warning_sent and elapsed >= warning_seconds:
                warning_sent = True
                self.status.emit(self._stall_message())

            if elapsed >= timeout_seconds:
                self.error.emit(self._timeout_message())
                return

        try:
            if self._cancel_event.is_set() or self.isInterruptionRequested():
                self.cancelled.emit()
                return

            if "error" in error_holder:
                raise error_holder["error"]

            response_text = result_holder.get("response", "")
            # Format the response into the standard message dictionary structure.
            new_message = {'role': 'assistant', 'content': response_text}
            self.finished.emit(new_message)
        except Exception as e:
            if isinstance(e, api_provider.RequestCancelledError):
                self.cancelled.emit()
                return
            # If any exception occurs during the agent's execution, emit an error signal.
            self.error.emit(str(e))

    def _watchdog_limits(self):
        if self._contains_audio_attachment():
            return 60, 1800
        return 35, 420

    def _contains_audio_attachment(self):
        for message in self.conversation_history:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "audio_file":
                    return True
        return False

    def _stall_message(self):
        if self._contains_audio_attachment():
            return (
                "Audio is still being processed. This can take a while for long clips. "
                "You'll get the response or a clear failure message automatically."
            )
        return "This request is taking longer than expected, but it is still running."

    def _timeout_message(self):
        if self._contains_audio_attachment():
            return (
                "Audio processing stalled before the model returned a response.\n\n"
                "Please try again. If this keeps happening, use a shorter clip or switch to an audio-capable Gemini or Ollama model."
            )
        return (
            "The model stopped responding before the request completed.\n\n"
            "Please try again or choose a faster model."
        )


def clean_agent_markdown_response(text, required_title, section_markers, reset_bullet_state_on_section_header=False):
    """Strip common markdown noise and normalize bullets/section spacing for a
    structured agent response (Explainer/KeyTakeaway/GroupSummary all used a
    near-identical ~40-line clean_text() before this was extracted).

    Args:
        text (str): The raw text from the AI model.
        required_title (str): Header line to prepend if the first cleaned line
            doesn't already contain it.
        section_markers (list[str]): Line substrings (e.g. "Key Parts:") that get
            an extra blank line before them.
        reset_bullet_state_on_section_header (bool): Whether encountering a
            section-marker line resets bullet-run tracking (GroupSummaryAgent did;
            ExplainerAgent/KeyTakeawayAgent didn't - preserved here so extracting
            this doesn't change any of their three outputs).

    Returns:
        str: The cleaned and formatted text.
    """
    # Remove markdown and special characters that might interfere with display.
    replacements = [
        ('```', ''),
        ('`', ''),
        ('**', ''),
        ('__', ''),
        ('*', ''),
        ('_', ''),
        ('•', '•'),
        ('→', '->'),
        ('\n\n\n', '\n\n'),
    ]

    cleaned = text
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)

    # Process line by line for finer control.
    cleaned_lines = []
    for line in cleaned.split('\n'):
        line = line.strip()
        if line:
            # Standardize bullet points.
            if line.lstrip().startswith('-'):
                line = '• ' + line.lstrip('- ')
            cleaned_lines.append(line)

    # Rebuild the text with consistent spacing and headers.
    formatted = ''
    in_bullet_list = False

    for i, line in enumerate(cleaned_lines):
        # Ensure the required header is present.
        if i == 0 and required_title not in line:
            formatted += f"{required_title}\n"

        # Add line with proper spacing based on its content type.
        if line.startswith('•'):
            if not in_bullet_list:
                formatted += '\n' if formatted else ''
            in_bullet_list = True
            formatted += line + '\n'
        elif any(marker in line for marker in section_markers):
            formatted += '\n' + line + '\n'
            if reset_bullet_state_on_section_header:
                in_bullet_list = False
        else:
            in_bullet_list = False
            formatted += line + '\n'

    return formatted.strip()


class ExplainerAgent:
    """An agent specialized in simplifying complex topics."""
    def __init__(self):
        """Initializes the ExplainerAgent with a highly structured system prompt."""
        self.system_prompt = """You are an expert at explaining complex topics in simple terms. Follow these principles in order:

1. Simplification: Break down complex ideas into their most basic form
2. Clarification: Remove any technical jargon or complex terminology
3. Distillation: Extract only the most important concepts
4. Breakdown: Present information in small, digestible chunks
5. Simple Language: Use everyday words and short sentences

Always use:
- Analogies: Connect ideas to everyday experiences
- Metaphors: Compare complex concepts to simple, familiar things

Format your response exactly like this:

Simple Explanation
[2-3 sentence overview using everyday language]

Think of it Like This:
[Add one clear analogy or metaphor that a child would understand]

Key Parts:
• [First simple point]
• [Second simple point]
• [Third point if needed]

Remember: Write as if explaining to a curious 5-year-old. No technical terms, no complex words."""
        
    def clean_text(self, text):
        """
        Cleans and formats the raw AI response to ensure it adheres to the
        expected structure for display in a Note item.

        Args:
            text (str): The raw text from the AI model.

        Returns:
            str: The cleaned and formatted text.
        """
        return clean_agent_markdown_response(
            text,
            required_title="Simple Explanation",
            section_markers=['Think of it Like This:', 'Key Parts:'],
        )

    def get_response(self, text):
        """
        Generates a simplified explanation for the given text.

        Args:
            text (str): The text to explain.

        Returns:
            str: The simplified explanation.
        """
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': f"Explain this in simple terms: {text}"}
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        raw_response = response['message']['content']
        
        # Clean and format the final response.
        formatted_response = self.clean_text(raw_response)
        return formatted_response


class ExplainerWorkerThread(QThread):
    """QThread worker for the ExplainerAgent."""
    finished = Signal(str, QPointF)
    error = Signal(str)
    
    def __init__(self, agent, text, node_pos):
        super().__init__()
        self.agent = agent
        self.text = text
        self.node_pos = node_pos
        self._is_running = True
        
    def run(self):
        """Executes the agent's logic and emits the result."""
        try:
            if not self._is_running: return
            response = self.agent.get_response(self.text)
            if self._is_running:
                self.finished.emit(response, self.node_pos)
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False
            
    def stop(self):
        """Stops the thread safely."""
        self._is_running = False


class KeyTakeawayAgent:
    """An agent specialized in extracting key takeaways from a block of text."""
    def __init__(self):
        """Initializes the KeyTakeawayAgent with its structured system prompt."""
        self.system_prompt = """You are a key takeaway generator. Format your response exactly like this:

Key Takeaway
[1-2 sentence overview]

Main Points:
• [First key point]
• [Second key point]
• [Third key point if needed]

Keep total output under 150 words. Be direct and focused on practical value.
No markdown formatting, no special characters."""
        
    def clean_text(self, text):
        """
        Cleans and formats the raw AI response to fit the expected structure.

        Args:
            text (str): The raw text from the AI model.

        Returns:
            str: The cleaned and formatted text.
        """
        return clean_agent_markdown_response(
            text,
            required_title="Key Takeaway",
            section_markers=['Main Points:'],
        )

    def get_response(self, text):
        """
        Generates key takeaways for the given text.

        Args:
            text (str): The text to summarize.

        Returns:
            str: The formatted key takeaways.
        """
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': f"Generate key takeaways from this text: {text}"}
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        raw_response = response['message']['content']
        
        # Clean and format the final response.
        formatted_response = self.clean_text(raw_response)
        return formatted_response


class KeyTakeawayWorkerThread(QThread):
    """QThread worker for the KeyTakeawayAgent."""
    finished = Signal(str, QPointF)  # Signal includes response and node position
    error = Signal(str)
    
    def __init__(self, agent, text, node_pos):
        """
        Initializes the worker.

        Args:
            agent (KeyTakeawayAgent): The agent instance.
            text (str): The text to process.
            node_pos (QPointF): The position of the source node.
        """
        super().__init__()
        self.agent = agent
        self.text = text
        self.node_pos = node_pos
        self._is_running = True
        
    def run(self):
        """Executes the agent's logic and emits the result."""
        try:
            if not self._is_running: return
            response = self.agent.get_response(self.text)
            if self._is_running:
                self.finished.emit(response, self.node_pos)
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False
            
    def stop(self):
        """Stops the thread safely."""
        self._is_running = False


class GroupSummaryAgent:
    """An agent that synthesizes multiple text snippets into a single cohesive summary."""
    def __init__(self):
        """Initializes the GroupSummaryAgent with its synthesis-focused system prompt."""
        self.system_prompt = """You are a synthesis expert. Your task is to analyze a collection of separate text snippets and generate a single, cohesive summary.

RULES:
1.  **Do Not Summarize Individually:** Your goal is NOT to create a list of summaries for each snippet.
2.  **Find the Connection:** Read all snippets to understand the underlying theme, argument, or narrative that connects them.
3.  **Synthesize:** Weave the key information from all snippets into a single, flowing summary.
4.  **Be Cohesive:** The final output should read like a standalone piece of text that makes sense without seeing the original snippets.
5.  **Format your response exactly like this:**

Synthesized Summary
[A concise paragraph that combines the core ideas from all provided texts.]

Key Connected Points:
• [First synthesized point]
• [Second synthesized point]
• [Third synthesized point if needed]
"""

    def clean_text(self, text):
        """
        Cleans and formats the raw AI response to fit the expected structure.

        Args:
            text (str): The raw text from the AI model.

        Returns:
            str: The cleaned and formatted text.
        """
        return clean_agent_markdown_response(
            text,
            required_title="Synthesized Summary",
            section_markers=['Key Connected Points:'],
            reset_bullet_state_on_section_header=True,
        )

    def get_response(self, texts: list):
        """
        Generates a synthesized summary from a list of text snippets.

        Args:
            texts (list[str]): A list of strings to synthesize.

        Returns:
            str: The synthesized summary.
        """
        # Combine the list of texts into a single string for the prompt,
        # clearly delineating each snippet.
        combined_text = ""
        for i, text in enumerate(texts):
            combined_text += f"--- Snippet {i+1} ---\n{text}\n\n"

        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': f"Synthesize the following text snippets into a single summary:\n\n{combined_text}"}
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        raw_response = response['message']['content']
        return self.clean_text(raw_response)


class GroupSummaryWorkerThread(QThread):
    """QThread worker for the GroupSummaryAgent."""
    finished = Signal(str, QPointF, list)
    error = Signal(str)

    def __init__(self, agent, texts, node_pos, source_nodes):
        """
        Initializes the worker.

        Args:
            agent (GroupSummaryAgent): The agent instance.
            texts (list[str]): The texts to summarize.
            node_pos (QPointF): The desired position for the resulting summary note.
            source_nodes (list): The original nodes being summarized, to create connections.
        """
        super().__init__()
        self.agent = agent
        self.texts = texts
        self.node_pos = node_pos
        self.source_nodes = source_nodes
        self._is_running = True

    def run(self):
        """Executes the agent's logic and emits the result."""
        try:
            if not self._is_running: return
            response = self.agent.get_response(self.texts)
            if self._is_running:
                self.finished.emit(response, self.node_pos, self.source_nodes)
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False

    def stop(self):
        """Stops the thread safely."""
        self._is_running = False
