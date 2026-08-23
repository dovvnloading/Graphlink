"""Multimodal content codec for the scene domain (ADR-002 stage 2.2).

Relocated VERBATIM from backend/canvas.py (its lines 61-155; originally
ported there from graphlink_app/graphlink_session/content_codec.py at R7.2
- the block comment below carries that full history). Pure
base64/binascii/logging only - no backend infrastructure - which is what
qualifies it for backend/domain/ under tests/test_domain_purity.py.

The _content_codec SimpleNamespace at the bottom is deliberately a SINGLE
shared instance: backend/session_load.py and backend/session_save.py reach
it through backend.canvas's own re-import (unchanged spelling
`_content_codec.foo(...)`), and object identity must be preserved - a
second instance would silently fork that seam.
"""

from __future__ import annotations

import base64
import binascii
import logging
from types import SimpleNamespace

# R7.2: ported from graphlink_app/graphlink_session/content_codec.py, not
# imported. content_codec.py itself is 100% Qt-free (base64/binascii/logging
# only), but the graphlink_session PACKAGE it lives in is not -
# graphlink_session/__init__.py eagerly imports ChatSessionManager and
# SaveWorkerThread, and workers.py in turn imports PySide6.QtCore, so a plain
# `from graphlink_session.content_codec import ...` would run that __init__.py
# first (Python always runs a package's __init__.py, even for a submodule
# import) and pull Qt into backend/'s import graph. Before R7.2 this was
# loaded via a raw importlib.util.spec_from_file_location path load,
# bypassing the package's __init__.py - that workaround existed only because
# every OTHER Qt-free survivor could be reached by relocating the physical
# file (matching R7.2's real destination for the 33 modules it did move); this
# one file can't get the same treatment because graphlink_session/
# deserializers.py and serializers.py - Qt-tainted, still live until the R7.6
# cutover - import it via a normal package-relative
# `from graphlink_session.content_codec import (...)`, which a physical move
# would break. So the file itself stays exactly where it is, and its logic is
# ported here instead - the same "reimplement, don't import" precedent
# backend/chat_library.py and backend/crash_recovery.py already follow for a
# Qt-tainted wrapper around Qt-free logic. The _content_codec namespace below
# exists purely so every one of the 8 call sites across canvas.py/
# session_load.py/session_save.py keeps its existing `_content_codec.foo(...)`
# spelling unchanged.


def _serialize_history(history):
    serialized_history = []
    for message in history or []:
        new_message = message.copy()
        if "content" in new_message:
            new_message["content"] = _process_content_for_serialization(new_message["content"])
        serialized_history.append(new_message)
    return serialized_history


def _deserialize_history(history):
    deserialized_history = []
    for message in history or []:
        new_message = message.copy()
        if "content" in new_message:
            new_message["content"] = _process_content_for_deserialization(new_message["content"])
        deserialized_history.append(new_message)
    return deserialized_history


def _process_content_for_serialization(content):
    """Base64-encode raw image bytes inside multimodal content payloads."""
    if isinstance(content, list):
        processed_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_bytes" and isinstance(part.get("data"), bytes):
                new_part = part.copy()
                new_part["data"] = base64.b64encode(part["data"]).decode("utf-8")
                processed_parts.append(new_part)
            else:
                processed_parts.append(part)
        return processed_parts
    return content


def _process_content_for_deserialization(content):
    """Decode base64 image payloads back into raw bytes when loading a chat."""
    if isinstance(content, list):
        processed_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_bytes" and isinstance(part.get("data"), str):
                new_part = part.copy()
                try:
                    new_part["data"] = base64.b64decode(part["data"])
                    processed_parts.append(new_part)
                except (binascii.Error, ValueError):
                    logging.exception("Failed to decode base64 image data during deserialization.")
                    processed_parts.append({"type": "text", "text": "[ERROR: Image Data Corrupted]"})
            elif isinstance(part, dict) and part.get("type") == "audio_file":
                # SECURITY-FIX: an audio_file part is a bare filesystem PATH
                # that the provider layer opens lazily at SEND time
                # (api_provider._read_attachment_bytes, openai_provider's
                # input_audio, the Gemini Files upload). An image is
                # persisted as its own base64 BYTES (decoded just above), so
                # it is self-contained and safe to reload; audio persists
                # only the path, and nothing records which paths the user
                # actually staged, so once a chat is on disk there is no way
                # to tell a user-attached path from one an attacker wrote
                # into the row. A hostile chats.db row or imported .graphlink
                # archive could carry {"type":"audio_file","path":"<any local
                # file, e.g. an SSH key>"} - invisible in the UI (the SPA
                # never renders audio_file, and the text mirror shows
                # nothing) - and the first follow-up turn on that branch
                # would read that file and ship it to the model provider
                # (uploaded to Google, for Gemini). Reloaded audio is
                # therefore neutralized to an inert text placeholder here,
                # at the load-from-disk boundary: a live staged attachment
                # in the SAME session still sends normally (it never passes
                # through deserialization), but no path from persisted data
                # is ever handed back to the provider layer.
                processed_parts.append({"type": "text", "text": "[Audio attachment - reattach to include it]"})
            else:
                processed_parts.append(part)
        return processed_parts
    return content


def _encode_image_bytes(data):
    return base64.b64encode(data).decode("utf-8")


def _decode_image_bytes(data):
    return base64.b64decode(data)


_content_codec = SimpleNamespace(
    serialize_history=_serialize_history,
    deserialize_history=_deserialize_history,
    process_content_for_serialization=_process_content_for_serialization,
    process_content_for_deserialization=_process_content_for_deserialization,
    encode_image_bytes=_encode_image_bytes,
    decode_image_bytes=_decode_image_bytes,
)
