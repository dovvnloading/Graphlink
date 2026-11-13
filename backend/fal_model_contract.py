"""Extract the selected Fal endpoint's JSON input and final output from OpenAPI."""
from __future__ import annotations

from collections import deque
from urllib.parse import urlsplit

from backend.fal_integration import FalError, model_path
from backend.fal_json import select_pointer

# Only descend into schema-valued keywords. An input field literally named
# "examples", or an object-valued default, is part of the model's contract.
_SCHEMA_MAPS = {"properties", "patternProperties", "dependentSchemas", "$defs", "definitions"}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_VALUES = {
    "additionalProperties", "additionalItems", "unevaluatedProperties", "unevaluatedItems",
    "propertyNames", "not", "if", "then", "else", "contains", "items",
}
_ANNOTATIONS = {"title", "examples", "example", "x-fal-order-properties"}


def _get(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _resolve(document, value):
    """Follow Reference Objects locally. Do not request external schemas."""
    seen = set()
    while isinstance(value, dict) and set(value) == {"$ref"}:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            break
        seen.add(ref)
        try:
            value = select_pointer(document, ref[1:])
        except FalError:
            break
    return value


def _json_schema(document, body):
    schema = _get(_resolve(document, body), "content", "application/json", "schema")
    return schema if isinstance(schema, (dict, bool)) else None


def _success_schema(document, operation):
    responses = _get(operation, "responses")
    if not isinstance(responses, dict):
        return None
    for status in sorted(responses):
        if status.startswith("2"):
            schema = _json_schema(document, responses[status])
            if schema is not None:
                return schema
    return None


def _routes(document, absolute):
    # Some specifications put owner/app in servers.url and use relative paths.
    yield absolute
    servers = document.get("servers")
    for server in servers if isinstance(servers, list) else []:
        url = _get(server, "url")
        if not isinstance(url, str):
            continue
        try:
            prefix = urlsplit(url).path.rstrip("/")
        except ValueError:
            continue
        if prefix and (absolute == prefix or absolute.startswith(prefix + "/")):
            yield absolute[len(prefix):] or "/"


def _compact(schema, pending, depth=0):
    if depth > 100:
        raise FalError("Fal model schema exceeds the maximum nesting depth.")
    if not isinstance(schema, dict):
        return schema
    compact = {}
    for key, value in schema.items():
        if key in _ANNOTATIONS:
            continue
        if key == "$ref" and isinstance(value, str):
            pending.append(value)
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            value = {name: _compact(item, pending, depth + 1) for name, item in value.items()}
        elif key in _SCHEMA_LISTS or key == "items" and isinstance(value, list):
            if isinstance(value, list):
                value = [_compact(item, pending, depth + 1) for item in value]
        elif key in _SCHEMA_VALUES:
            value = _compact(value, pending, depth + 1)
        compact[key] = value
    return compact


def model_contract(details: dict) -> dict:
    """Return the input, output, and reachable named schemas for one exact model."""
    model = model_path(_get(details, "endpoint_id"))
    document = _get(details, "openapi")
    paths = _get(document, "paths")
    if not isinstance(paths, dict) or not paths:
        raise FalError("Fal did not provide an OpenAPI schema for this model endpoint.")

    absolute = "/" + model
    submit_route = next((route for route in _routes(document, absolute)
                         if isinstance(_get(paths, route, "post"), dict)), None)
    submit = _get(paths, submit_route, "post")
    input_schema = _json_schema(document, _get(submit, "requestBody"))

    # Fal publishes nested model result schemas at either the full model path
    # or the owner/app queue path. The submit response describes the queue job.
    base = "/" + "/".join(model.split("/")[:2])
    suffix = "/requests/{request_id}"
    output = None
    for prefix in dict.fromkeys((absolute, base)):
        for route in _routes(document, prefix + suffix):
            output = _success_schema(document, _get(paths, route, "get"))
            if output is not None:
                break
        if output is not None:
            break

    # Synchronous specifications publish the model output on the POST.
    servers = document.get("servers")
    if output is None and isinstance(servers, list) and any(
        isinstance(_get(server, "url"), str)
        and (server["url"].rstrip("/") == "https://fal.run" or server["url"].startswith("https://fal.run/"))
        for server in servers
    ):
        output = _success_schema(document, submit)

    if input_schema is None and output is None:
        raise FalError("Fal did not publish the selected endpoint's JSON input or output schema.")
    warnings = [f"Fal did not publish the {name} schema for this endpoint."
                for name, schema in (("input", input_schema), ("output", output)) if schema is None]
    pending: deque[str] = deque()
    contract = {
        "model_id": model,
        "input": _compact(_resolve(document, input_schema), pending),
        "output": _compact(_resolve(document, output), pending),
    }
    components = {}
    unresolved = []
    seen = set()
    while pending:
        ref = pending.popleft()
        if ref in seen:
            continue
        seen.add(ref)
        if ref.startswith("#/components/schemas/"):
            # A ref can point inside a component. Keep the original component
            # and pointer so refs resolve against this result just as in Fal.
            pointer = "/".join(ref[1:].split("/")[:4])
            name = pointer.split("/")[-1].replace("~1", "/").replace("~0", "~")
            try:
                select_pointer(document, ref[1:])  # verify the full reference
                if name not in components:
                    components[name] = _compact(select_pointer(document, pointer), pending)
                continue
            except FalError:
                pass
        unresolved.append(ref)
    if components:
        contract["components"] = {"schemas": components}
    if warnings:
        contract["warnings"] = warnings
    if unresolved:
        contract["unresolved_refs"] = unresolved
    return contract
