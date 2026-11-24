"""Run an operator-approved Library preset on the already-running backend."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from backend.control import DEFAULT_CONTROL_PORT, DEFAULT_TOKEN_FILE
from backend.mcp_server import ControlError, ControlHttpClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=f"http://127.0.0.1:{DEFAULT_CONTROL_PORT}")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--write-token-file", type=Path, required=True)
    parser.add_argument("--workspace-id", type=int, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    run = commands.add_parser("run")
    run.add_argument("preset_id")
    run.add_argument("--request-json", help="Exact approved workflow ID, revision, inputs and parameters as JSON")
    run.add_argument("--operation-id", help="Reuse the same ID to retry without another run")
    read = commands.add_parser("read")
    read.add_argument("run_id")
    args = parser.parse_args(argv)
    try:
        if args.write_token_file.expanduser().resolve() == args.token_file.expanduser().resolve():
            raise ValueError("Read and write token files must differ.")
        client = ControlHttpClient(args.url, args.token_file.expanduser().read_text(encoding="utf-8").strip(),
                                   args.write_token_file.expanduser().read_text(encoding="utf-8").strip())
        shared = {"workspace_id": args.workspace_id}
        if args.command == "list":
            result = client.read("/api/control/workflows", shared, write_capability=True)
        elif args.command == "run":
            operation_id = args.operation_id or uuid.uuid4().hex
            body = {**shared, "preset_id": args.preset_id, "operation_id": operation_id}
            if args.request_json is not None:
                if not isinstance(json.loads(args.request_json), dict):
                    raise ValueError("Request JSON must be an object.")
                body["request_json"] = args.request_json
            sys.stderr.write(f"Operation ID: {operation_id}\n")
            result = client.write("/api/control/workflows/run", body)
        else:
            result = client.read("/api/control/workflows/run", {**shared, "run_id": args.run_id},
                                 write_capability=True)
        sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
        return 0
    except (ControlError, OSError, ValueError) as exc:
        sys.stderr.write(f"Graphlink workflow command failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
