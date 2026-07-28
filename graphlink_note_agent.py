"""Qt-free note-agent core: Key Takeaway and Explainer Note (R8a).

Both agents were implemented in the Qt app's graphlink_agents_core.py and
were lost to the R7.6b cutover, leaving their two chat-node menu items
rendered as disabled stubs whose tooltip blamed a missing agent layer. That
blocker had in fact been gone since R4 - the same dispatcher that already
drives Regenerate Response, Generate Image and Generate Chart. Nothing was
blocked; the two agents had simply never been ported. This module restores
them.

The prompts, the `clean_agent_markdown_response` post-processor and the
"one chat call, no tools, no history" shape are carried over verbatim from
the deleted implementation, so output matches what the Qt app produced.

Two deliberate divergences from that original, both because the machinery
it depended on no longer exists:

  - No text bounding. Legacy passed source text through
    `render_context(source_snapshot(...))`, which died with the Qt app and
    has no successor. Every surviving agent path (chart, image, chat) feeds
    unbounded text today, so this matches them rather than reintroducing a
    single bounded path. If context limits ever need enforcing, that is a
    systemic concern for all agents, not one this feature should solve
    alone.
  - No QThread workers. The dispatcher owns concurrency now
    (backend/agents.py's start_note_generation), so the legacy
    `KeyTakeawayWorkerThread`/`ExplainerWorkerThread` classes have no
    counterpart and are not ported.

This file must stay Qt-free forever - it exists to be importable from
backend/, which test_no_qt_anywhere.py holds to zero tolerance.
"""

import api_provider

# graphlink_task_config (NOT graphlink_config) - the R4.1 Qt-free split of
# task/provider/model config. graphlink_config chains to PySide6.QtGui, so
# importing it here would silently re-taint this module with Qt.
import graphlink_task_config as config


def clean_agent_markdown_response(
    text,
    required_title,
    section_markers,
    reset_bullet_state_on_section_header=False,
):
    """Strip common markdown noise and normalize bullets/section spacing for a
    structured agent response.

    Ported verbatim from the deleted graphlink_agents_core.py, including the
    `reset_bullet_state_on_section_header` flag: the legacy GroupSummaryAgent
    set it and the two agents here did not, and that difference is preserved
    rather than normalized away so their output is unchanged. (Group Summary
    itself is not ported - its menu entry was conditional on a multi-select
    model the new stack does not have.)

    Args:
        text (str): The raw text from the AI model.
        required_title (str): Header line to prepend if the first cleaned line
            doesn't already contain it.
        section_markers (list[str]): Line substrings (e.g. "Key Parts:") that
            get an extra blank line before them.
        reset_bullet_state_on_section_header (bool): Whether a section-marker
            line resets bullet-run tracking.

    Returns:
        str: The cleaned and formatted text.
    """
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

    cleaned = str(text or "")
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)

    cleaned_lines = []
    for line in cleaned.split('\n'):
        line = line.strip()
        if line:
            if line.lstrip().startswith('-'):
                line = '• ' + line.lstrip('- ')
            cleaned_lines.append(line)

    formatted = ''
    in_bullet_list = False

    for i, line in enumerate(cleaned_lines):
        if i == 0 and required_title not in line:
            formatted += f"{required_title}\n"

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


class KeyTakeawayAgent:
    """Extracts key takeaways from a block of text."""

    def __init__(self):
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
        return clean_agent_markdown_response(
            text,
            required_title="Key Takeaway",
            section_markers=['Main Points:'],
        )

    def get_response(self, text):
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': f"Generate key takeaways from this text: {text}"},
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        return self.clean_text(response['message']['content'])


class ExplainerAgent:
    """Simplifies complex topics into plain language."""

    def __init__(self):
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
        return clean_agent_markdown_response(
            text,
            required_title="Simple Explanation",
            section_markers=['Think of it Like This:', 'Key Parts:'],
        )

    def get_response(self, text):
        messages = [
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': f"Explain this in simple terms: {text}"},
        ]
        response = api_provider.chat(task=config.TASK_CHAT, messages=messages)
        return self.clean_text(response['message']['content'])
