"""Translate Graphlink tool names at the cloud API boundary.

Keep the registry, approval prompts and saved conversations in Graphlink's
names. Cloud function declarations and replayed calls share a restricted
identifier alphabet, so translate both together for each request.
"""

from __future__ import annotations

import re
from dataclasses import replace

from backend.providers.base import ToolSpec


def cloud_tool_names(
    tools: tuple[ToolSpec, ...], messages: list[dict],
) -> tuple[tuple[ToolSpec, ...], list[dict], dict[str, str]]:
    names = {tool.name for tool in tools}
    for message in messages:
        if message.get("role") == "tool" and isinstance(message.get("name"), str):
            names.add(message["name"])
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or ():
                names.add(call["name"])

    # Reserve legal originals first. A plugin named fal_submit must never
    # shadow fal.submit when decoding a response, regardless of registry order.
    legal = {name for name in names if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name)}
    aliases = {name: name for name in legal}
    used = set(legal)
    for name in sorted(names - legal):
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:64] or "tool"
        if not re.match(r"[A-Za-z_]", stem):
            stem = ("_" + stem)[:64]
        alias = stem
        counter = 1
        while alias in used:
            suffix = f"_{counter}"
            alias = stem[:64 - len(suffix)] + suffix
            counter += 1
        aliases[name] = alias
        used.add(alias)

    wire_tools = tuple(
        tool if aliases[tool.name] == tool.name else replace(
            tool, name=aliases[tool.name], description=f"{tool.name}: {tool.description}",
        )
        for tool in tools
    )
    wire_messages = []
    for message in messages:
        if message.get("role") == "tool" and message.get("name") in aliases:
            message = {**message, "name": aliases[message["name"]]}
        elif message.get("role") == "assistant" and message.get("tool_calls"):
            calls = message["tool_calls"]
            message = {**message, "tool_calls": [
                {**call, "name": aliases[call["name"]]} for call in calls
            ]}
            # Claude's signed thinking and adjacent native tool_use blocks
            # are recorded in the ORIGINAL request's wire aliases. Those
            # aliases can be reassigned when tools change (fs.write versus
            # a newly offered fs_write). Reconcile by the canonical call ID,
            # not by the old alias, preserving all opaque thinking/signatures
            # and the original block order. Never mutate persisted history.
            blocks = message.get("_anthropic_content_blocks")
            if isinstance(blocks, list):
                uses = [block for block in blocks if isinstance(block, dict)
                        and block.get("type") == "tool_use"]
                valid = (
                    len(uses) == len(calls)
                    and all(
                        block.get("id") == call.get("id")
                        and isinstance(block.get("name"), str) and bool(block["name"])
                        and block.get("input") == call.get("arguments")
                        for block, call in zip(uses, calls)
                    )
                )
                if valid:
                    names_by_id = {call["id"]: aliases[call["name"]] for call in calls}
                    message["_anthropic_content_blocks"] = [
                        {**block, "name": names_by_id[block["id"]]}
                        if block.get("type") == "tool_use" else block
                        for block in blocks
                    ]
                else:
                    # Inconsistent replay metadata must fall back to the
                    # canonical ordinary tool_calls, never transmit a
                    # block that could claim the wrong historical action.
                    message.pop("_anthropic_content_blocks", None)
        wire_messages.append(message)
    return wire_tools, wire_messages, {aliases[tool.name]: tool.name for tool in tools}
