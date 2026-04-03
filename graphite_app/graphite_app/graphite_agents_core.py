import json
import re
import threading
import time
from PySide6.QtCore import QThread, Signal, QPointF
import graphite_config as config
import api_provider
from graphite_widgets import TokenEstimator
from graphite_memory import trim_history

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
        
    def run(self):
        """
        The main execution method for the thread. This is called when the thread starts.
        It runs the agent and emits the result.
        """
        result_holder = {}
        error_holder = {}

        def _invoke():
            try:
                result_holder["response"] = self.agent.get_response(self.conversation_history, self.current_node)
            except Exception as exc:
                error_holder["error"] = exc

        worker = threading.Thread(target=_invoke, daemon=True)
        worker.start()

        warning_seconds, timeout_seconds = self._watchdog_limits()
        warning_sent = False
        started_at = time.monotonic()

        while worker.is_alive():
            worker.join(0.25)
            elapsed = time.monotonic() - started_at

            if not warning_sent and elapsed >= warning_seconds:
                warning_sent = True
                self.status.emit(self._stall_message())

            if elapsed >= timeout_seconds:
                self.error.emit(self._timeout_message())
                return

        try:
            if "error" in error_holder:
                raise error_holder["error"]

            response_text = result_holder.get("response", "")
            # Format the response into the standard message dictionary structure.
            new_message = {'role': 'assistant', 'content': response_text}
            self.finished.emit(new_message)
        except Exception as e:
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
                "You’ll get the response or a clear failure message automatically."
            )
        return "This request is taking longer than expected, but it is still running."

    def _timeout_message(self):
        if self._contains_audio_attachment():
            return (
                "Audio processing stalled before the model returned a response.\n\n"
                "Please try again. If this keeps happening, use a shorter clip or switch to Gemini native audio."
            )
        return (
            "The model stopped responding before the request completed.\n\n"
            "Please try again or choose a faster model."
        )


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
        
    def run(self, conversation_history, current_node):
        """
        Executes the chat logic for a single turn.

        Args:
            conversation_history (list): The list of messages in the conversation.
            current_node (QGraphicsItem): The current node context to check for custom prompts.

        Returns:
            str: The AI-generated response text.

        Raises:
            Exception: Propagates exceptions from the API provider.
        """
        final_system_prompt = self.system_prompt
        use_system_prompt = bool((self.system_prompt or "").strip())

        if use_system_prompt and current_node:
            # Traverse up the node hierarchy to find the root of the current branch.
            root_node = current_node
            while root_node.parent_node:
                root_node = root_node.parent_node
            
            # Check if the root node has a custom system prompt note attached.
            if root_node.scene():
                for conn in root_node.scene().system_prompt_connections:
                    if conn.end_node == root_node:
                        prompt_note = conn.start_node
                        if prompt_note.content:
                            final_system_prompt = prompt_note.content
                        break

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
            
            response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
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
        
    def get_response(self, conversation_history, current_node):
        """
        Gets an AI response for a given conversation history.

        Args:
            conversation_history (list): The list of messages in the conversation.
            current_node (QGraphicsItem): The current node context.

        Returns:
            str: The AI-generated response text.
        """
        # This agent is stateless. It does not store conversation_history.
        # It creates a temporary ChatWorker to handle the API call.
        chat_worker = ChatWorker(self.system_prompt)
        ai_response = chat_worker.run(conversation_history, current_node)
        return ai_response


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
            
        # Split into lines and clean each line individually.
        lines = cleaned.split('\n')
        cleaned_lines = []
        
        for line in lines:
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
            # Ensure the "Simple Explanation" title is present.
            if i == 0 and "Simple Explanation" not in line:
                formatted += "Simple Explanation\n"
                
            # Add line with proper spacing based on its content type.
            if line.startswith('•'):
                if not in_bullet_list:
                    formatted += '\n' if formatted else ''
                in_bullet_list = True
                formatted += line + '\n'
            elif any(section in line for section in ['Think of it Like This:', 'Key Parts:']):
                formatted += '\n' + line + '\n'
            else:
                in_bullet_list = False
                formatted += line + '\n'
        
        return formatted.strip()

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
        # A series of replacements to strip unwanted formatting.
        replacements = [
            ('```', ''),  # code blocks
            ('`', ''),    # inline code
            ('**', ''),   # bold
            ('__', ''),   # alternate bold
            ('*', ''),    # italic/bullet
            ('_', ''),    # alternate italic
            ('•', '•'),   # standardize bullets
            ('→', '->'),  # standardize arrows
            ('\n\n\n', '\n\n'),  # remove extra newlines
        ]
        
        cleaned = text
        for old, new in replacements:
            cleaned = cleaned.replace(old, new)
            
        # Process line by line for finer control.
        lines = cleaned.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            if line:
                # Ensure bullet points are properly formatted.
                if line.lstrip().startswith('-'):
                    line = '• ' + line.lstrip('- ')
                cleaned_lines.append(line)
        
        # Rebuild the text with consistent spacing and headers.
        formatted = ''
        in_bullet_list = False
        
        for i, line in enumerate(cleaned_lines):
            # Ensure the main title is present.
            if i == 0 and "Key Takeaway" not in line:
                formatted += "Key Takeaway\n"
                
            # Add line with proper spacing.
            if line.startswith('•'):
                if not in_bullet_list:
                    formatted += '\n' if formatted else ''
                in_bullet_list = True
                formatted += line + '\n'
            elif 'Main Points:' in line:
                formatted += '\n' + line + '\n'
            else:
                in_bullet_list = False
                formatted += line + '\n'
        
        return formatted.strip()

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
        replacements = [
            ('```', ''), ('`', ''), ('**', ''), ('__', ''), ('*', ''), ('_', ''),
            ('•', '•'), ('→', '->'), ('\n\n\n', '\n\n'),
        ]
        cleaned = text
        for old, new in replacements:
            cleaned = cleaned.replace(old, new)
        
        lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        cleaned_lines = []
        for line in lines:
            if line.lstrip().startswith('-'):
                cleaned_lines.append('• ' + line.lstrip('- '))
            else:
                cleaned_lines.append(line)

        formatted = ''
        in_bullet_list = False
        for i, line in enumerate(cleaned_lines):
            if i == 0 and "Synthesized Summary" not in line:
                formatted += "Synthesized Summary\n"
            
            if line.startswith('•'):
                if not in_bullet_list:
                    formatted += '\n'
                in_bullet_list = True
                formatted += line + '\n'
            elif "Key Connected Points:" in line:
                formatted += '\n' + line + '\n'
                in_bullet_list = False
            else:
                in_bullet_list = False
                formatted += line + '\n'
        
        return formatted.strip()

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
