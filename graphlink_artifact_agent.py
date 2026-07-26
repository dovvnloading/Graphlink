"""Qt-free artifact-agent core (Qt-removal plan R5.2 prerequisite).

`ArtifactAgent` moved out of graphlink_agents_artifact.py: that module's
unconditional `from PySide6.QtCore import QThread, Signal` (needed only by
its `ArtifactWorkerThread` class) meant importing anything from it -
including this Qt-free class - pulled PySide6 into the process. That made it
unimportable from backend/ despite containing zero Qt code itself, exactly
the same problem R4.2 fixed for chat by splitting graphlink_chat_agent.py out
of graphlink_agents_core.py (see that module's own docstring).

graphlink_agents_artifact.py re-exports this class unchanged for its own
ArtifactWorkerThread (which constructs one internally) and the legacy Qt call
site (graphlink_window_actions.py's execute_artifact_node) - see its own
import line.

This file must stay Qt-free forever - it exists to be importable from
backend/, which test_no_qt_anywhere.py holds to zero tolerance.
"""

import re

import graphlink_task_config as config
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
