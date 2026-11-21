"""ADR-019 live-browser perf support: seed real chats, serve the real SPA, read process RSS.

This module is a benchmark launcher, NOT a production module. The only
substitution is an offline, paced chat completion in the benchmark process so
a re-run measures Graphlink's real re-run/WS/render path without an API key,
network request, model latency, or paid inference.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import itertools
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


def seed(profile: Path, node_count: int) -> None:
    from backend.chat_library import save_chat_atomically_row
    from backend.session_save import build_chat_data
    from backend.tests.perf.graph_factory import LARGE, TYPICAL

    workload = {100: TYPICAL, 500: LARGE}[node_count]
    profile.mkdir(parents=True, exist_ok=True)
    db_path = profile / "chats.db"

    def save(title: str, document) -> int:
        data = build_chat_data(document)
        notes_data = data.pop("notes_data", [])
        pins_data = data.pop("pins_data", [])
        chat_id, _ = save_chat_atomically_row(db_path, None, title, data, notes_data, pins_data)
        return chat_id

    # Seed a *real* saved row using the production serializer and SQLite
    # writer. No benchmark time includes fixture construction or DB seeding.
    normal = workload.build()
    normal_id = save(f"ADR019 {node_count} nodes load", normal)

    # The real re-run intent requires an edited user turn (versionCount > 1).
    # Node 15 is an ordinary user turn in both factory graphs, with descendant
    # assistant replies; keep this scene a full-sized, saved factory fixture.
    rerun = workload.build()
    user = list(rerun.nodes.values())[15]
    if user.kind != "chat" or not user.state.is_user:
        raise RuntimeError("graph factory user-turn index 15 changed")
    rerun.edit_chat_message(
        user.id, f"ADR019_RERUN_ROOT_{node_count}: edited benchmark prompt", user.content,
    )
    rerun_id = save(f"ADR019 {node_count} nodes rerun", rerun)

    sys.stdout.write(json.dumps({
        "name": f"live_{workload.name}",
        "node_count": len(normal.nodes),
        "edge_count": len(normal.edges),
        "load_chat_id": normal_id,
        "rerun_chat_id": rerun_id,
    }) + "\n")


def serve(profile: Path, port: int) -> None:
    import uvicorn

    from backend.agents import AgentDispatcher
    from backend.app import create_app

    # The real re-run orchestration and all app/WS/React code still execute.
    # Replace ONLY the external model completion in this isolated test
    # process. A paced 1200-character reply produces visible scene publishes
    # without credentials or nondeterministic provider latency.
    serial = itertools.count(1)

    async def scripted_start_chat_reply(_self, **kwargs):
        kwargs["on_begin"](uuid.uuid4().hex)

        async def complete():
            try:
                await asyncio.sleep(0.12)
                answer = ("ADR-019 reproducible reply %d. " % next(serial)).ljust(1200, "x")
                await kwargs["on_reply"](answer)
            finally:
                kwargs["on_end"]()

        asyncio.create_task(complete())

    AgentDispatcher.start_chat_reply = scripted_start_chat_reply
    app = create_app(
        spa_dir=Path(__file__).resolve().parents[3] / "web_ui" / "dist" / "app",
        settings_state_file=profile / "session.dat",
        chat_db_path=profile / "chats.db",
        knowledge_db_path=profile / "knowledge.db",
        auth_token=None,
        allow_unauthenticated_for_tests=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def rss_bytes(pid: int) -> int:
    """Operating-system resident/working-set bytes, not Python tracemalloc."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.restype = wintypes.BOOL
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                raise OSError(ctypes.get_last_error(), f"GetProcessMemoryInfo({pid}) failed")
            return counters.working_set_size
        finally:
            kernel.CloseHandle(handle)

    if sys.platform.startswith("linux"):
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
        raise RuntimeError(f"VmRSS unavailable for process {pid}")

    # macOS: ps RSS is in KiB. The scheduled workflow runs on Windows.
    if sys.platform == "darwin":
        value = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True).strip()
        return int(value) * 1024
    raise RuntimeError(f"unsupported platform for RSS: {sys.platform}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    seed_cmd = sub.add_parser("seed")
    seed_cmd.add_argument("--profile", type=Path, required=True)
    seed_cmd.add_argument("--nodes", type=int, choices=(100, 500), required=True)
    serve_cmd = sub.add_parser("serve")
    serve_cmd.add_argument("--profile", type=Path, required=True)
    serve_cmd.add_argument("--port", type=int, required=True)
    rss_cmd = sub.add_parser("rss")
    rss_cmd.add_argument("--pids", type=int, nargs="+", required=True)
    args = parser.parse_args()
    if args.mode == "seed":
        seed(args.profile, args.nodes)
    elif args.mode == "serve":
        serve(args.profile, args.port)
    else:
        sys.stdout.write(json.dumps({str(pid): rss_bytes(pid) for pid in args.pids}) + "\n")


if __name__ == "__main__":
    main()
