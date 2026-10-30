import copy
import json
import re

from graphlink_prompts import _TokenBytesEncoder
from graphlink_token_estimator import estimate_image_tokens


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


def message_tokens(message, token_estimator):
    """One history message's prompt-token estimate: its text (role, text
    parts, structure) through the text tokenizer, plus each image_bytes
    part's vision cost from graphlink_token_estimator.estimate_image_tokens.

    The image payload is taken OUT of the text view before tokenizing - its
    bytes (or base64 text) say nothing about what the image costs a model,
    which is a per-image figure derived from its dimensions. A placeholder-
    only count budgets a full-size image at a few dozen tokens and lets an
    image-heavy history overflow the window; counting its base64 as text
    would over-budget it a hundredfold."""
    content = message.get("content") if isinstance(message, dict) else None
    image_tokens = 0
    text_view = message
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_bytes":
                image_tokens += estimate_image_tokens(part.get("data"))
                part = {key: value for key, value in part.items() if key != "data"}
            text_parts.append(part)
        text_view = {**message, "content": text_parts}
    text_tokens = token_estimator.count_tokens(json.dumps(text_view, cls=_TokenBytesEncoder))
    return text_tokens + image_tokens


def trim_history(history, token_estimator, max_tokens=8000, system_prompt_estimate=500, reserve_tokens=0):
    normalized_history = clone_history(history)
    current_tokens = max(0, system_prompt_estimate) + max(0, reserve_tokens)
    # Each kept message travels with its own token count, so every removal
    # below (the leading-assistant pop) and every re-add (the keep-newest
    # fallback) moves its tokens with it: the returned context_tokens is
    # always exactly the sum over the messages actually returned - never a
    # count that still includes a popped turn or counts the newest twice.
    kept = []

    for message in reversed(normalized_history):
        tokens = message_tokens(message, token_estimator)
        if current_tokens + tokens > max_tokens:
            break
        kept.append((message, tokens))
        current_tokens += tokens
    kept.reverse()

    if kept and kept[0][0].get("role") == "assistant":
        kept.pop(0)

    # ADR-006 stage 6.8 review fix (keep-newest guarantee): the newest
    # message is ALWAYS kept, even when it alone exceeds the budget - the
    # provider seeing an over-budget final question beats the model never
    # seeing the question at all (and beats it being replaced by a short
    # summary of itself). Applied AFTER the leading-assistant pop so a
    # single oversized newest message survives regardless of role.
    if not kept and normalized_history:
        newest = normalized_history[-1]
        kept = [(newest, message_tokens(newest, token_estimator))]

    trimmed_history = [message for message, _ in kept]
    context_tokens = sum(tokens for _, tokens in kept)
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
