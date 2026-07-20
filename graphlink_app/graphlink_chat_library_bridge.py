"""Desktop-side state bridge for the chat-library island.

Phase 4 increment 4 - the most complex Phase 4 surface: real session-DB CRUD
against ChatDatabase (reached through `session_manager.db`), plus two intents
that defer to the ChatWindow. The native frameless-drag QDialog shell is kept
around this bridge's web host (see graphlink_chat_library_web.py and the
rewritten ChatLibraryDialog), so this bridge owns only the list/search/CRUD
content, never the window chrome.

`_format_timestamp` is moved here verbatim from the legacy ChatLibraryDialog -
timestamps ship as pre-formatted display strings, so the web side never needs
the stored format. Delete/rename CONFIRMATION lives entirely client-side (a
two-step in-UI confirm, mirroring the settings island's API-Reset pattern);
Python performs the mutation only once the web side truly confirms, and never
sees an intermediate "confirm requested" state. `new_chat()`'s own native
QMessageBox stays native and untouched, reached through
`session_manager.window.new_chat(...)`.

loadChat/newChat are deferred one event-loop tick (QTimer.singleShot(0, ...))
before doing heavy scene work / popping new_chat()'s modal - the same caution
command-palette's executeCommand takes for callbacks that pop dialogs, kept
out of the QWebChannel slot invocation. delete/rename are pure fast DB writes
plus a republish, so they stay synchronous like the settings write-slots.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from graphlink_island_bridge import IslandBridge


def _format_timestamp(value) -> str:
    """Moved verbatim from ChatLibraryDialog - the stored format is
    sqlite's `"%Y-%m-%d %H:%M:%S"`; unparseable/empty values echo back
    unchanged, matching the legacy behavior exactly."""
    if not value:
        return "Unknown"
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%b %d, %Y %I:%M %p")
    except ValueError:
        return str(value)


class ChatLibraryBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, session_manager, library_dialog, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._session_manager = session_manager
        # The native shell that hosts this island's web content. Used to close
        # the whole dialog on a successful load / new-chat, and as the
        # parent_for_dialog so new_chat()'s native confirm centers over the
        # library window exactly as legacy did.
        self._library_dialog = library_dialog
        self._notice: str | None = None

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        notice = self._notice
        rows: list[dict[str, Any]] = []
        try:
            for chat_id, title, created_at, updated_at in self._session_manager.db.get_all_chats():
                rows.append(
                    {
                        "id": int(chat_id),
                        "title": str(title),
                        "createdLabel": _format_timestamp(created_at),
                        "updatedLabel": _format_timestamp(updated_at),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as a recoverable notice
            # The list itself couldn't be read - recoverable inline message
            # replaces the legacy QMessageBox.critical, keeping the surface up.
            rows = []
            notice = f"Could not load saved chats: {exc}"
        return {"rows": rows, "notice": notice}

    @Slot()
    def ready(self):
        self.publish()

    @Slot()
    def refresh(self):
        """Re-read the DB and republish. Not needed at open (construct-per-open
        already gives a fresh ready() snapshot), but harmless and useful if the
        web side ever wants an explicit re-list."""
        self._notice = None
        self.publish()

    @Slot(int)
    def loadChat(self, chat_id: int):
        # Deferred out of the QWebChannel slot: load_chat rebuilds the whole
        # scene, and on success closes (deletes) the dialog whose web host is
        # mid-slot - the same deferral command-palette's executeCommand uses.
        QTimer.singleShot(0, partial(self._perform_load_chat, int(chat_id)))

    def _perform_load_chat(self, chat_id: int):
        if self.disposed:
            return
        try:
            self._session_manager.load_chat(chat_id)
            window = getattr(self._session_manager, "window", None)
            if window is not None:
                window.update_title_bar()
            self._close_dialog()
        except Exception as exc:  # noqa: BLE001 - recoverable, shown inline
            self._notice = f"Failed to load chat: {exc}"
            self.publish()

    @Slot(int)
    def deleteChat(self, chat_id: int):
        """The web side only calls this after its own two-step confirm, so no
        confirmation happens here (the legacy QMessageBox.question moved fully
        client-side). Pure fast DB write + republish, synchronous."""
        self._session_manager.db.delete_chat(int(chat_id))
        self._notice = None
        self.publish()

    @Slot(int, str)
    def renameChat(self, chat_id: int, new_title: str):
        """Non-empty guard matches the legacy `if ok and new_title:` - an
        empty/whitespace title is ignored, no mutation, no error (the web side
        disables Save for an empty draft anyway)."""
        title = str(new_title or "").strip()
        if not title:
            return
        self._session_manager.db.rename_chat(int(chat_id), title)
        self._notice = None
        self.publish()

    @Slot()
    def newChat(self):
        # Deferred: new_chat() pops a native modal QMessageBox, which must not
        # run inside the QWebChannel slot invocation.
        QTimer.singleShot(0, self._perform_new_chat)

    @Slot()
    def close(self):
        """Closes the whole native dialog. Needed because the legacy
        Escape-to-close (ChatLibraryDialog.keyPressEvent) only fires while a
        NATIVE widget has focus - once the web search/rename input has focus,
        Chromium traps the key and it never reaches the native dialog. The web
        side's Escape handler calls this to preserve Escape-to-close from
        anywhere in the content."""
        self._close_dialog()

    def _perform_new_chat(self):
        if self.disposed:
            return
        window = getattr(self._session_manager, "window", None)
        if window is not None and hasattr(window, "new_chat"):
            if window.new_chat(parent_for_dialog=self._library_dialog):
                self._close_dialog()

    def _close_dialog(self):
        dialog = self._library_dialog
        if dialog is None:
            return
        try:
            dialog.close()
        except (RuntimeError, AttributeError):
            # C++ side already gone (e.g. torn down between the deferred tick
            # and here) - nothing to close.
            pass
