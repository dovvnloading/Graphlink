import copy
import json
import re

from graphlink_prompts import _TokenBytesEncoder


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

    # ADR-006 stage 6.8 review fix (keep-newest guarantee): the newest
    # message is ALWAYS kept, even when it alone exceeds the budget - the
    # provider seeing an over-budget final question beats the model never
    # seeing the question at all (and beats it being replaced by a short
    # summary of itself). Applied AFTER the leading-assistant pop so a
    # single oversized newest message survives regardless of role.
    if not trimmed_history and normalized_history:
        newest = normalized_history[-1]
        trimmed_history = [newest]
        current_tokens += token_estimator.count_tokens(
            json.dumps(newest, cls=_TokenBytesEncoder)
        )

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
                elif item_type == "audio_file":
                    parts.append(f"[Audio Attachment: {item.get('name') or 'audio'}]")
        return "\n".join(part for part in parts if part)

    return str(content)
