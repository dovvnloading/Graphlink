"""Tests for backend/native_dialogs.py (Qt-removal plan R7.4c).

webview.windows is monkeypatched directly (a plain list, per pywebview's
own source) rather than mocking a whole Window class where not needed -
the "no window" branch only cares that the list is empty; the "window
exists" branch needs just enough of a fake Window to satisfy
create_file_dialog's call signature.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import webview

from backend import native_dialogs


class _FakeWindow:
    def __init__(self, return_value):
        self.return_value = return_value
        self.calls = []

    def create_file_dialog(self, dialog_type=10, directory="", allow_multiple=False, save_filename="", file_types=()):
        self.calls.append(
            {
                "dialog_type": dialog_type,
                "directory": directory,
                "allow_multiple": allow_multiple,
                "save_filename": save_filename,
                "file_types": file_types,
            }
        )
        return self.return_value


def test_pick_file_returns_none_when_no_window_exists(monkeypatch):
    monkeypatch.setattr(webview, "windows", [])

    result = asyncio.run(native_dialogs.pick_file(file_types=("GGUF files (*.gguf)",)))

    assert result is None


def test_pick_file_calls_open_dialog_with_file_types_and_returns_first_path(monkeypatch):
    fake = _FakeWindow(return_value=("C:/models/a.gguf",))
    monkeypatch.setattr(webview, "windows", [fake])

    result = asyncio.run(native_dialogs.pick_file(file_types=("GGUF files (*.gguf)",)))

    assert result == "C:/models/a.gguf"
    assert fake.calls == [
        {
            "dialog_type": webview.FileDialog.OPEN,
            "directory": "",
            "allow_multiple": False,
            "save_filename": "",
            "file_types": ("GGUF files (*.gguf)",),
        }
    ]


def test_pick_file_returns_none_when_the_user_cancels(monkeypatch):
    fake = _FakeWindow(return_value=None)
    monkeypatch.setattr(webview, "windows", [fake])

    result = asyncio.run(native_dialogs.pick_file())

    assert result is None


def test_pick_folder_returns_none_when_no_window_exists(monkeypatch):
    monkeypatch.setattr(webview, "windows", [])

    result = asyncio.run(native_dialogs.pick_folder())

    assert result is None


def test_pick_folder_calls_folder_dialog_and_returns_first_path(monkeypatch):
    fake = _FakeWindow(return_value=("C:/models",))
    monkeypatch.setattr(webview, "windows", [fake])

    result = asyncio.run(native_dialogs.pick_folder())

    assert result == "C:/models"
    assert fake.calls[0]["dialog_type"] == webview.FileDialog.FOLDER


def test_pick_folder_returns_none_when_the_user_cancels(monkeypatch):
    fake = _FakeWindow(return_value=None)
    monkeypatch.setattr(webview, "windows", [fake])

    result = asyncio.run(native_dialogs.pick_folder())

    assert result is None


def test_pick_save_file_returns_none_when_no_window_exists(monkeypatch):
    monkeypatch.setattr(webview, "windows", [])

    result = asyncio.run(native_dialogs.pick_save_file("Default.graphlink"))

    assert result is None


def test_pick_save_file_calls_save_dialog_with_default_name_and_returns_the_path(monkeypatch):
    # Unlike pick_file/pick_folder's own tuple-wrapped return, a real
    # pywebview SAVE dialog resolves to a plain string - see pick_save_file's
    # own docstring for why.
    fake = _FakeWindow(return_value="C:/exports/My Workspace.graphlink")
    monkeypatch.setattr(webview, "windows", [fake])

    result = asyncio.run(
        native_dialogs.pick_save_file("My Workspace.graphlink", file_types=("Graphlink Archive (*.graphlink)",))
    )

    assert result == "C:/exports/My Workspace.graphlink"
    assert fake.calls == [
        {
            "dialog_type": webview.FileDialog.SAVE,
            "directory": "",
            "allow_multiple": False,
            "save_filename": "My Workspace.graphlink",
            "file_types": ("Graphlink Archive (*.graphlink)",),
        }
    ]


def test_pick_save_file_returns_none_when_the_user_cancels(monkeypatch):
    fake = _FakeWindow(return_value=None)
    monkeypatch.setattr(webview, "windows", [fake])

    result = asyncio.run(native_dialogs.pick_save_file("Default.graphlink"))

    assert result is None


def test_pick_save_file_returns_none_when_the_dialog_resolves_to_an_empty_string(monkeypatch):
    # Defensive: some pywebview builds resolve a cancelled SAVE dialog to ""
    # rather than None - pick_save_file's own `result or None` must treat
    # both the same way.
    fake = _FakeWindow(return_value="")
    monkeypatch.setattr(webview, "windows", [fake])

    result = asyncio.run(native_dialogs.pick_save_file("Default.graphlink"))

    assert result is None


def test_dialog_calls_run_on_the_dedicated_executor_not_the_default_one(monkeypatch):
    # SECURITY-FIX regression (native-dialog-intents-pin-default-executor-threads):
    # dialogs used to go through asyncio.to_thread, which always runs on the
    # loop's shared DEFAULT executor - the same pool ~177 other to_thread call
    # sites depend on. Confirms run_in_executor is now called with the
    # module's own _DIALOG_EXECUTOR (not None, which asyncio.to_thread would
    # pass to mean "use the default executor").
    fake = _FakeWindow(return_value=("C:/models/a.gguf",))
    monkeypatch.setattr(webview, "windows", [fake])

    seen_executors = []

    async def run():
        loop = asyncio.get_running_loop()
        real_run_in_executor = loop.run_in_executor

        def recording_run_in_executor(executor, func, *args):
            seen_executors.append(executor)
            return real_run_in_executor(executor, func, *args)

        monkeypatch.setattr(loop, "run_in_executor", recording_run_in_executor)
        return await native_dialogs.pick_file(file_types=("GGUF files (*.gguf)",))

    result = asyncio.run(run())

    assert result == "C:/models/a.gguf"
    assert seen_executors == [native_dialogs._DIALOG_EXECUTOR]
    assert seen_executors[0] is not None


def test_dialog_executor_is_a_small_dedicated_pool_distinct_from_the_default_executor():
    # The whole fix is that this is NOT the same object asyncio.to_thread(...)
    # would use (None -> the loop's shared default ThreadPoolExecutor) - a
    # dedicated, small pool keeps a stuck/open dialog from wedging the ~177
    # other to_thread call sites across the backend that share the default one.
    assert isinstance(native_dialogs._DIALOG_EXECUTOR, ThreadPoolExecutor)
    assert native_dialogs._DIALOG_EXECUTOR._max_workers == 4


def test_uses_the_first_window_when_multiple_exist():
    # webview.windows can only ever grow (create_window appends, nothing
    # removes) - confirms the module reads index 0, not "the last one" or
    # anything order-sensitive beyond that documented convention.
    first = _FakeWindow(return_value=("first-picked.gguf",))
    second = _FakeWindow(return_value=("second-picked.gguf",))
    import webview as wv

    original = wv.windows
    try:
        wv.windows = [first, second]
        result = asyncio.run(native_dialogs.pick_file())
        assert result == "first-picked.gguf"
        assert second.calls == []
    finally:
        wv.windows = original
