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
            message = {**message, "tool_calls": [
                {**call, "name": aliases[call["name"]]} for call in message["tool_calls"]
            ]}
        wire_messages.append(message)
    return wire_tools, wire_messages, {aliases[tool.name]: tool.name for tool in tools}
