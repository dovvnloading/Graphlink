"""No-credential, loopback-only HTTP transport for explicit Graph Runner Ollama calls.

Avoid the Ollama SDK's global client and its ambient OLLAMA_API_KEY header:
the graph's local-only adapter must never inherit a cloud credential, proxy,
redirect policy, or stale endpoint from the rest of the process.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def local_ollama_complete(*, prompt: str, model_id: str, endpoint: str) -> dict[str, Any]:
    """Make one bounded, no-auth local chat call through an explicit HTTP client.

    The caller MUST validate and pin the numeric loopback endpoint, authorize
    the exact model, and checkpoint the request identity before calling us.
    No credentials, cookies or environment-derived proxy settings are sent.
    """
    request = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    with httpx.Client(
        base_url=endpoint,
        trust_env=False,
        follow_redirects=False,
        timeout=120.0,
        headers={"Accept": "application/json"},
    ) as client:
        with client.stream("POST", "/api/chat", json=request) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise ValueError("The local model response exceeded the 2 MiB limit.")
                content.extend(chunk)

    try:
        result = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("The local model returned invalid JSON.") from exc
    message = result.get("message") if isinstance(result, dict) else None
    answer = message.get("content") if isinstance(message, dict) else None
    if not isinstance(answer, str):
        raise ValueError("The local model returned an invalid chat response.")
    return {"message": {"role": "assistant", "content": answer}}
