"""An explicitly loopback-only Ollama adapter for opt-in Graph Runner model calls.

Use a dedicated SDK client: ollama.chat is bound to a process-global Client
created at import time, which may have captured a different OLLAMA_HOST. The
one-shot graph workflow must not inherit an unrelated provider configuration
or environment proxy. This adapter creates no remote/cloud provider calls.
"""

from __future__ import annotations

from typing import Any

import ollama


def local_ollama_complete(*, prompt: str, model_id: str, endpoint: str) -> dict[str, Any]:
    """Make one bounded local chat call with redirects and proxies disabled.

    The caller MUST validate endpoint as loopback, pin an exact model and
    checkpoint its request identity before calling this function.
    """
    with ollama.Client(
        host=endpoint,
        trust_env=False,
        follow_redirects=False,
        timeout=120.0,
    ) as client:
        response = client.chat(
            model=model_id, messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
    return {
        "message": {
            "role": "assistant",
            "content": response.message.content,
        },
    }
