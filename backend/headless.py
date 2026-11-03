"""Headless Graphlink: the existing FastAPI backend without a pywebview.

Run python -m backend.headless (see docs/HEADLESS_MCP.md). It binds
127.0.0.1 exclusively, mints distinct full-access and read-only tokens on
every launch, and writes ONLY the read-only token to a restrictive local
file for authorized MCP clients,
and runs the SAME create_app() as the desktop shell. There is no alternate
SceneDocument, SQLite writer, or agent implementation here.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
from pathlib import Path

from backend.auth import mint_token
from backend.control import DEFAULT_CONTROL_PORT, DEFAULT_TOKEN_FILE


def _private_staging(path: Path) -> tuple[int, str]:
    """Create a secure staging file BEFORE writing any credential bytes.

    Windows os.chmod does not set a file's DACL: the protected user-only
    security descriptor must be applied at CreateFileW time instead of
    inheriting permissions from a potentially public custom directory.
    """
    if os.name == "nt":
        from backend.windows_token_file import create_private_windows_staging

        return create_private_windows_staging(path)
    return tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)


def _write_token_file(path: Path, token: str) -> None:
    """Atomically publish a protected token file or fail closed."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise OSError(f"refusing token-file symlink: {path}")
    fd, staging = _private_staging(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(staging, 0o600)
        # Same-directory replacement retains the already-verified DACL of
        # Windows staging files. No secret is ever written to an ordinary
        # temporary file with inherited, possibly permissive ACLs.
        os.replace(staging, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        try:
            Path(staging).unlink(missing_ok=True)
        except OSError:
            pass


def _bind_listener(port: int) -> socket.socket:
    """Claim the loopback socket once, including exclusive binding on Windows."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
    except OSError:
        listener.close()
        raise
    return listener


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Graphlink without opening a desktop window")
    parser.add_argument("--port", type=int, default=DEFAULT_CONTROL_PORT, help="127.0.0.1 TCP port (default 8765)")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE, help="local per-launch token file")
    parser.add_argument("--chat-db", type=Path, default=None, help="optional chats.db location")
    parser.add_argument("--settings-file", type=Path, default=None, help="optional settings file")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    from contextlib import ExitStack

    from backend.chat_library import DEFAULT_DB_PATH
    from backend.instance_lock import DataDirectoryInUse, exclusive_data_directory

    # Acquire before socket binding, token rotation, or create_app()
    # initialization. A refused second instance must not invalidate the
    # FIRST process's still-live read-only MCP capability token.
    with ExitStack() as stack:
        try:
            stack.enter_context(exclusive_data_directory(
                args.chat_db if args.chat_db is not None else DEFAULT_DB_PATH
            ))
        except (DataDirectoryInUse, OSError) as exc:
            sys.stderr.write(f"Graphlink headless cannot start: {exc}\n")
            return 1
        return _run_headless_owned(args)


def _run_headless_owned(args: argparse.Namespace) -> int:
    import uvicorn
    from backend.app import create_app

    # Preserve the final path component: resolving it here would follow a
    # pre-existing symlink BEFORE _write_token_file can refuse it.
    token_file = args.token_file.expanduser().absolute()
    listener = _bind_listener(args.port)
    backend_token = mint_token()
    control_read_token = mint_token()
    try:
        # Never put the WebSocket/administrative credential on disk for
        # external MCP clients. Possession of the file grants only reads.
        try:
            _write_token_file(token_file, control_read_token)
        except OSError as exc:
            sys.stderr.write(f"Graphlink headless cannot secure MCP token file: {exc}\n")
            return 1
        app = create_app(
            auth_token=backend_token,
            control_read_token=control_read_token,
            chat_db_path=args.chat_db,
            settings_state_file=args.settings_file,
        )
        port = listener.getsockname()[1]
        sys.stderr.write(
            f"Graphlink headless listening on http://127.0.0.1:{port} "
            f"(read-only token in {token_file})\n"
        )
        sys.stderr.flush()
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning",
        ))
        server.run(sockets=[listener])
    finally:
        listener.close()
        # Do not delete a newer process's token if somebody restarted it.
        try:
            if not token_file.is_symlink() and token_file.read_text(encoding="utf-8").strip() == control_read_token:
                token_file.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
