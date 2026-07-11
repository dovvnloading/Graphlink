import base64
import binascii
import logging


def process_content_for_serialization(content):
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


def process_content_for_deserialization(content):
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
            else:
                processed_parts.append(part)
        return processed_parts
    return content


def serialize_history(history):
    serialized_history = []
    for message in history or []:
        new_message = message.copy()
        if "content" in new_message:
            new_message["content"] = process_content_for_serialization(new_message["content"])
        serialized_history.append(new_message)
    return serialized_history


def deserialize_history(history):
    deserialized_history = []
    for message in history or []:
        new_message = message.copy()
        if "content" in new_message:
            new_message["content"] = process_content_for_deserialization(new_message["content"])
        deserialized_history.append(new_message)
    return deserialized_history


def encode_image_bytes(data):
    return base64.b64encode(data).decode("utf-8")


def decode_image_bytes(data):
    return base64.b64decode(data)
