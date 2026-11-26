"""Decode OpenAI-compatible embedding responses into request order.

The response's ``data[].index`` identifies which input each vector belongs
to. Array order is not trusted: a proxy or compatible server that reorders,
duplicates or drops items would otherwise pair a valid-looking vector with
the wrong chunk, and the cache would mark it complete (#1086).
"""

from __future__ import annotations

from typing import Any


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def decode_openai_embeddings(response: Any, expected: int) -> list[Any]:
    """Return one embedding per request position, ordered by ``index``.

    Every item must carry a distinct integer index in ``range(expected)``.
    A response where *no* item has an index is the one explicit legacy
    shape and is taken in array order; a response that mixes indexed and
    index-less items is rejected. Vector contents are validated by the
    caller before anything is cached.
    """
    data = _field(response, "data")
    if not isinstance(data, list) or len(data) != expected:
        count = len(data) if isinstance(data, list) else "no"
        raise ValueError(
            f"Embedding response returned {count} item(s) for {expected} input(s); nothing was cached."
        )
    indices = [_field(item, "index") for item in data]
    if all(index is None for index in indices):
        return [_field(item, "embedding") for item in data]
    ordered: list[Any] = [None] * expected
    seen: set[int] = set()
    for item, index in zip(data, indices):
        if type(index) is not int or not 0 <= index < expected or index in seen:
            raise ValueError(
                "Embedding response has a missing, duplicate or out-of-range item index; "
                "nothing was cached. Check the embedding endpoint or proxy."
            )
        seen.add(index)
        ordered[index] = _field(item, "embedding")
    return ordered
