import copy
import json
import re

from graphite_prompts import _TokenBytesEncoder


def clone_history(history):
    if not history:
        return []

    cloned = []
    for message in history:
        if not isinstance(message, dict):
            continue
        if "role" not in message or "content" not in message:
            continue
        cloned.append({
            "role": message["role"],
            "content": copy.deepcopy(message["content"])
        })
    return cloned


def append_history(history, additions):
    combined = clone_history(history)
    combined.extend(clone_history(additions))
    return combined


def assign_history(target_node, history):
    if hasattr(target_node, "conversation_history"):
        target_node.conversation_history = clone_history(history)


def resolve_context_anchor(node):
    cursor = node
    seen = set()
    fallback = None

    while cursor and id(cursor) not in seen:
        seen.add(id(cursor))

        if hasattr(cursor, "conversation_history"):
            if fallback is None:
                fallback = cursor
            if getattr(cursor, "conversation_history", None):
                return cursor

        cursor = getattr(cursor, "parent_content_node", None) or getattr(cursor, "parent_node", None)

    return fallback


def resolve_branch_parent(node):
    if node is None:
        return None

    if hasattr(node, "children"):
        return node

    parent_content_node = getattr(node, "parent_content_node", None)
    if parent_content_node is not None and hasattr(parent_content_node, "children"):
        return parent_content_node

    parent_node = getattr(node, "parent_node", None)
    if parent_node is not None and hasattr(parent_node, "children"):
        return parent_node

    return None


def get_node_history(node):
    context_anchor = resolve_context_anchor(node)
    if not context_anchor or not hasattr(context_anchor, "conversation_history"):
        return []
    return clone_history(getattr(context_anchor, "conversation_history", []))


def trim_history(history, token_estimator, max_tokens=8000, system_prompt_estimate=500, reserve_tokens=0):
    normalized_history = clone_history(history)
    current_tokens = max(0, system_prompt_estimate) + max(0, reserve_tokens)
    trimmed_history = []

    for message in reversed(normalized_history):
        message_tokens = token_estimator.count_tokens(json.dumps(message, cls=_TokenBytesEncoder))
        if current_tokens + message_tokens > max_tokens:
            break
        trimmed_history.insert(0, message)
        current_tokens += message_tokens

    if trimmed_history and trimmed_history[0].get("role") == "assistant":
        trimmed_history.pop(0)

    context_tokens = max(0, current_tokens - system_prompt_estimate - reserve_tokens)
    return trimmed_history, context_tokens


def history_to_transcript(history, max_messages=10, max_chars_per_message=900):
    recent_history = clone_history(history)[-max_messages:]
    if not recent_history:
        return "No prior branch context."

    transcript_parts = []
    for message in recent_history:
        role = str(message.get("role", "unknown")).title()
        content = _flatten_content(message.get("content", ""))
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        if len(content) > max_chars_per_message:
            content = content[: max_chars_per_message - 3].rstrip() + "..."
        if content:
            transcript_parts.append(f"{role}: {content}")

    return "\n\n".join(transcript_parts) if transcript_parts else "No prior branch context."


def _flatten_content(content):
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(str(item.get("text", "")))
                elif item_type == "image_bytes":
                    parts.append("[Image Attachment]")
        return "\n".join(part for part in parts if part)

    return str(content)
