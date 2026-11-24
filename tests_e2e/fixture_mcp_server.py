"""Deterministic stdio MCP fixture for the real browser workflow; no network."""
from __future__ import annotations

import json
import sys


TOOL = {
    "name": "lookup_records", "title": "Look up records",
    "description": "Return a small project table for a supplied query.",
    "inputSchema": {
        "type": "object", "properties": {
            "query": {"type": "string", "title": "Query", "minLength": 1},
            "limit": {"type": "integer", "title": "Limit", "default": 3, "minimum": 1, "maximum": 5},
            "include_closed": {"type": "boolean", "title": "Include closed", "default": False},
        }, "required": ["query"], "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object", "properties": {
            "rows": {"type": "array", "items": {"type": "object", "properties": {
                "Project": {"type": "string"}, "Status": {"type": "string"}, "Count": {"type": "integer"},
            }, "required": ["Project", "Status", "Count"]}},
            "summary": {"type": "string"},
        }, "required": ["rows", "summary"],
    }, "annotations": {"readOnlyHint": True, "destructiveHint": False},
}


def main() -> None:
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "graphlink-e2e-records", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            args = request["params"]["arguments"]
            query = args["query"]
            rows = [{"Project": f"{query} {index + 1}", "Status": "Open", "Count": (index + 1) * 7}
                    for index in range(args.get("limit", 3))]
            summary = f"Found {len(rows)} records for {query}."
            result = {"content": [{"type": "text", "text": summary}],
                      "structuredContent": {"rows": rows, "summary": summary}, "isError": False}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
