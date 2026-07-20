"""Web host for the chat-library island - Phase 4 increment 4.

A genuine hybrid, unlike every prior island: the native frameless-drag
`ChatLibraryDialog` (QDialog) shell is retained exactly as-is and this host
is embedded INSIDE it, occupying only the content region below the native
title bar. So this host is a plain embedded child `QFrame` (no `Window`
flag, `corner_radius=0`, never a top-level window) - the same embedding
shape as DocumentViewerWebHost, not a floating Tool window like About/Help.

Because it's embedded, it never receives a native `closeEvent` and needs no
hide-not-teardown override. But unlike DocumentViewer it is CONSTRUCT-PER-OPEN
(the native dialog is `WA_DeleteOnClose`, rebuilt every `show_library()`), so
the rewritten dialog's `closeEvent` calls this host's `prepare_for_shutdown()`
explicitly, unregistering it from the shared shutdown registry each cycle
rather than leaking a dead reference into `_hosts`.
"""

from __future__ import annotations

from graphlink_chat_library_bridge import ChatLibraryBridge
from graphlink_web_island_host import WebIslandHost

CHAT_LIBRARY_UNAVAILABLE_MESSAGE = (
    "The chat library is unavailable because QtWebEngine failed to initialize."
)


class ChatLibraryWebHost(WebIslandHost):
    def __init__(self, session_manager, library_dialog, parent=None):
        bridge = ChatLibraryBridge(session_manager, library_dialog)
        super().__init__(
            bridge=bridge,
            asset_dir_name="chat-library",
            bridge_object_name="chatLibraryBridge",
            corner_radius=0,  # inset within the native shell; the shell owns rounding
            unavailable_message=CHAT_LIBRARY_UNAVAILABLE_MESSAGE,
            parent=parent,
        )
