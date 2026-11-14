"""Model catalogs whose endpoints differ from their chat API's wire format."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlencode


def catalog_pages(fetch: Callable[[dict], dict], params: dict, *, cursor_param: str,
                  next_cursor: Callable[[dict], str | None]) -> Iterator[dict]:
    """Fetch each page on the original endpoint. Treat a repeated cursor as a failed refresh."""
    seen: set[str] = set()
    while True:
        page = fetch(params)
        yield page
        cursor = next_cursor(page)
        if not cursor:
            return
        if cursor in seen:
            raise RuntimeError("The provider repeated a model catalog page token.")
        seen.add(cursor)
        params = {**params, cursor_param: cursor}


def native_chat_models(provider_slot: str, client: Any) -> list[str]:
    if provider_slot == "cohere":
        endpoint = "https://api.cohere.ai/v1/models"
        params = {"page_size": "1000", "endpoint": "chat"}
        cursor_param, response_cursor = "page_token", "next_page_token"
    else:
        # The Fireworks management API separates the public serverless library
        # from private account deployments. Private IDs remain manually selectable.
        endpoint = "https://api.fireworks.ai/v1/accounts/fireworks/models"
        params = {"pageSize": "200", "readMask": "name,supportsServerless,conversationConfig"}
        cursor_param, response_cursor = "pageToken", "nextPageToken"

    def fetch(query):
        return client.get(f"{endpoint}?{urlencode(query)}", cast_to=object, options={"timeout": 30.0})

    names = set()
    for page in catalog_pages(fetch, params, cursor_param=cursor_param, next_cursor=lambda p: p.get(response_cursor)):
        for model in page.get("models", []):
            if provider_slot == "fireworks" and not (model.get("supportsServerless") and model.get("conversationConfig")):
                continue
            if name := model.get("name"):
                names.add(name)
    return sorted(names, key=str.casefold)
