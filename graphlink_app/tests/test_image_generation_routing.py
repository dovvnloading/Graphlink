"""Regression coverage for explicit image-generation routing.

Chat text must always use the normal chat worker. Image generation is available
only through an explicit node action, so local/Ollama chat cannot be hijacked by
keyword matching and sent to the API-only image backend.
"""

import ast
from pathlib import Path


WINDOW_ACTIONS_PATH = Path(__file__).resolve().parents[1] / "graphlink_window_actions.py"


def _method_node(method_name):
    tree = ast.parse(WINDOW_ACTIONS_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            return node
    raise AssertionError(f"Could not find {method_name} in {WINDOW_ACTIONS_PATH}")


def test_send_message_does_not_silently_route_text_to_image_generation():
    send_message = _method_node("send_message")

    image_calls = [
        node
        for node in ast.walk(send_message)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "generate_image"
    ]

    assert not image_calls


def test_explicit_image_generation_action_remains_available():
    generate_image = _method_node("generate_image")
    assert generate_image.name == "generate_image"
