"""Artifact's LLM agent + background worker thread (Phase 7 prerequisite,
increment 2) - extracted from graphlink_plugins/graphlink_plugin_artifact.py,
mirroring the same split graphlink_agents_pycoder.py already did for PyCoder.

Both classes were already Qt-free/Qt-only-for-threading and self-contained -
this is a pure relocation, not a rewrite. ArtifactNode never referenced
either class by name in its own body (confirmed by grep); the only
production caller is graphlink_window_actions.py's execute_artifact_node,
which constructs an ArtifactWorkerThread and assigns it onto the node
(node.worker_thread) - that ownership shape is unchanged, only the import
path moves.
"""

import re
from PySide6.QtCore import QThread, Signal
import graphlink_config as config
import api_provider


class ArtifactAgent:
    """
    An agent specialized in iteratively creating and refining a living document.
    """
    def __init__(self):
        self.system_prompt = """You are an expert Document Drafting Assistant (Artifacts).
Your primary task is to create, update, or refine a 'living document' based on user instructions and the context of the conversation.

RULES:
1. You will receive the conversation history, and your system instructions contain the CURRENT state of the document.
2. You must output the ENTIRE updated document enclosed exactly within <artifact> and </artifact> tags. Do NOT truncate or abbreviate the document. If you are changing one paragraph, you must still output the whole document with that change applied.
3. After the </artifact> tag, provide a brief conversational response acknowledging the changes, explaining your thought process, or asking for clarification.
4. If the document is currently empty, create the first draft based entirely on the instruction.
5. Always use Markdown formatting for the document content.
"""

    def get_response(self, current_artifact, history):
        # We inject the document state directly into the system prompt to maintain clean alternating history
        system_with_doc = self.system_prompt + f"\n\n--- CURRENT DOCUMENT STATE ---\n{current_artifact if current_artifact else '(Document is currently empty)'}\n"

        messages = [{'role': 'system', 'content': system_with_doc}]
        for msg in history:
            messages.append(msg)

        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        raw_text = response['message']['content']

        # Parse out the artifact and the conversational response
        artifact_match = re.search(r'<artifact>(.*?)</artifact>', raw_text, re.DOTALL)
        if not artifact_match:
            # Previously fell back to treating the ENTIRE raw response - including any
            # conversational preamble/explanation the model wrote outside the tags - as
            # the new document body, silently corrupting the document on any tag-format
            # miss. Raising here instead routes through ArtifactWorkerThread's existing
            # except/error.emit path, which surfaces "Error: ..." in the node's chat and
            # leaves the previous document content untouched (see
            # _handle_artifact_error in graphlink_window_actions.py) rather than
            # overwriting it with something that was never meant to be the document.
            raise RuntimeError(
                "The model's response did not include the required <artifact>...</artifact> tags, "
                "so the document was left unchanged to avoid overwriting it with an unstructured reply."
            )

        new_artifact = artifact_match.group(1).strip()
        ai_message = raw_text.replace(artifact_match.group(0), "").strip()
        return new_artifact, ai_message


class ArtifactWorkerThread(QThread):
    finished = Signal(str, str)  # new_document, ai_message
    error = Signal(str)

    def __init__(self, current_artifact, history):
        super().__init__()
        self.current_artifact = current_artifact
        self.history = history
        self.agent = ArtifactAgent()
        self._is_running = True

    def run(self):
        try:
            if not self._is_running: return
            new_doc, ai_msg = self.agent.get_response(self.current_artifact, self.history)
            if self._is_running:
                self.finished.emit(new_doc, ai_msg)
        except Exception as e:
            if self._is_running:
                self.error.emit(str(e))
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False
