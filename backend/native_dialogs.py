"""Native OS file/folder picker capability (Qt-removal plan R7.4c).

The one genuinely NEW capability gap R7.4 scoping identified (not just a
port): Llama.cpp's GGUF picker and Ollama's folder-scan picker both need an
actual on-disk PATH string (llama.cpp's C++ bindings and Ollama's manifest
walker both need a real filesystem path, not file bytes) - a plain HTML
<input type="file"> only gives the browser-side bytes, never a path, so
this has to be a NATIVE OS dialog.

graphlink_desktop.py's webview.create_window(...) call discards its return
value, but that's a non-issue: pywebview's own webview.windows is a plain
module-level list that create_window() appends the new Window to
UNCONDITIONALLY, the moment it runs - reachable from anywhere afterward via
webview.windows[0], with zero plumbing changes needed in graphlink_desktop.py
itself. Window.create_file_dialog(...) is safe to call from a worker thread
(pywebview's own docs/exposed-JS-API pattern already does this) - confirmed
directly via inspect.signature and the @_shown_call wrapper it goes through,
which just waits on a plain threading.Event, not anything main-thread-only.

NO WINDOW is a normal, expected, gracefully-handled condition, not an error:
create_window() never runs under bare `uvicorn`/pytest (the packaged desktop
entry point is the only caller), so webview.windows is simply `[]` there.
Both functions below return None in that case - callers must treat a None
return exactly like a user-cancelled dialog (nothing was picked), which is
also what pywebview itself returns on cancel.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

import webview

# SECURITY-FIX (native-dialog-intents-pin-default-executor-threads): a native
# dialog blocks its worker thread until the user dismisses the OS picker -
# no timeout, no cap. asyncio.to_thread always runs on the loop's shared
# DEFAULT executor, the same pool ~177 other to_thread call sites across the
# backend depend on (settings run_locked, chat-library/autosave SQLite work,
# knowledge search, Ollama/llama.cpp scans). One dialog per WebSocket
# connection, with enough connections, exhausts that shared pool and stalls
# every other to_thread hop in the process until the dialogs are closed.
# A small dedicated pool keeps dialog threads off the shared one entirely;
# max_workers=4 is headroom above the one-dialog-at-a-time OS-modal reality,
# not an invitation to run more concurrently.
_DIALOG_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="native-dialog")


async def _run_in_dialog_executor(func, /, *args, **kwargs):
    """asyncio.to_thread's own implementation, minus its use of the shared
    default executor - pinned to _DIALOG_EXECUTOR instead (see the
    SECURITY-FIX comment above _DIALOG_EXECUTOR for why that distinction is
    the entire point). contextvars.copy_context() is carried over from
    asyncio.to_thread verbatim so callers see identical context propagation
    either way."""
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(_DIALOG_EXECUTOR, func_call)


def _active_window():
    return webview.windows[0] if webview.windows else None


async def pick_file(file_types: Sequence[str] = (), directory: str = "") -> str | None:
    """Opens a native OPEN file dialog. Returns the selected path, or None
    if no window exists (bare uvicorn/tests) or the user cancelled.

    `directory` seeds the dialog's starting location - matches legacy's own
    _pick_gguf_file, which always computed a real starting directory
    (the staged path's own folder, or a saved scan path, or home) rather
    than leaving it to whatever the OS defaults to."""
    window = _active_window()
    if window is None:
        return None
    result = await _run_in_dialog_executor(
        window.create_file_dialog,
        webview.FileDialog.OPEN,
        directory=directory,
        file_types=tuple(file_types),
    )
    return result[0] if result else None


async def pick_folder(directory: str = "") -> str | None:
    """Opens a native FOLDER dialog. Returns the selected path, or None if
    no window exists or the user cancelled."""
    window = _active_window()
    if window is None:
        return None
    result = await _run_in_dialog_executor(window.create_file_dialog, webview.FileDialog.FOLDER, directory=directory)
    return result[0] if result else None


async def pick_save_file(default_name: str, file_types: Sequence[str] = (), directory: str = "") -> str | None:
    """Opens a native SAVE file dialog (ADR-020 stage 20.5's own workspace
    export). Returns the chosen path, or None if no window exists or the
    user cancelled.

    Unlike pick_file/pick_folder above, pywebview's own SAVE dialog returns
    a single string (or None on cancel), not a one-element tuple - OPEN's
    and FOLDER's own tuple shape exists for their allow_multiple capability,
    which a save dialog has no equivalent of (there is only ever one
    destination path). `default_name` seeds the dialog's own filename field
    (pywebview's `save_filename` kwarg) - matches pick_file's own
    `directory` seeding precedent of not leaving a starting value to
    whatever the OS defaults to."""
    window = _active_window()
    if window is None:
        return None
    result = await _run_in_dialog_executor(
        window.create_file_dialog,
        webview.FileDialog.SAVE,
        directory=directory,
        save_filename=default_name,
        file_types=tuple(file_types),
    )
    return result or None
