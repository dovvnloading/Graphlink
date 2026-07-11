from graphlink_session.database import ChatDatabase
from graphlink_session.deserializers import SceneDeserializer
from graphlink_session.scene_index import has_saveable_nodes
from graphlink_session.serializers import SceneSerializer
from graphlink_session.title_generator import TitleGenerator
from graphlink_session.workers import SaveWorkerThread


class ChatSessionManager:
    """Coordinate scene persistence and chat session lifecycle."""

    def __init__(self, window):
        self.window = window
        self.db = ChatDatabase()
        self.title_generator = TitleGenerator(getattr(window, "settings_manager", None))
        self.current_chat_id = None
        self.save_thread = None
        self._is_saving = False
        self._save_pending = False
        # A background save serializes the chat that was active when it STARTED. If the
        # user switches chats (new chat / load) while that save is in flight, its result
        # is stale: applying its returned id would restore the previous chat as "active"
        # and the next autosave would then overwrite the wrong chat's row (silent data
        # loss). `_context_epoch` bumps on every switch; `_saving_epoch` records the epoch
        # a save started under, and _on_save_finished only adopts the result if they still
        # match. See doc/ARCHITECTURE_REVIEW_FINDINGS.md.
        self._context_epoch = 0
        self._saving_epoch = 0
        self.serializer = SceneSerializer(window) if window else None
        self.deserializer = SceneDeserializer(window) if window else None

    def _ensure_runtime_helpers(self):
        if self.window and self.serializer is None:
            self.serializer = SceneSerializer(self.window)
        if self.window and self.deserializer is None:
            self.deserializer = SceneDeserializer(self.window)

    def _show_status(self, message, tone="warning"):
        if self.window and hasattr(self.window, "notification_banner"):
            self.window.notification_banner.show_message(message, 6000, tone)
        else:
            print(message)

    def _scene_has_content(self, scene):
        return bool(scene and scene.items())

    def mark_context_switch(self):
        """Signal that the active chat is changing (new chat, load chat, etc.).

        Any background save already running was serializing the PREVIOUS chat; bumping
        the epoch lets _on_save_finished recognize its result as stale so it does not
        restore the previous chat_id over the newly-active one. Callers that reassign
        `current_chat_id` for a context switch (ChatWindow.new_chat, load_chat) must call
        this. See doc/ARCHITECTURE_REVIEW_FINDINGS.md.
        """
        self._context_epoch += 1

    def load_chat(self, chat_id):
        chat = self.db.load_chat(chat_id)
        if not chat:
            return None

        # Switching away from whatever is currently active - invalidate any in-flight save.
        self.mark_context_switch()

        if not self.window:
            self.current_chat_id = chat_id
            return chat

        self.current_chat_id = None
        self._ensure_runtime_helpers()
        notes_data = self.db.load_notes(chat_id)
        pins_data = self.db.load_pins(chat_id)
        loaded = self.deserializer.restore_chat(chat, notes_data, pins_data)
        if not loaded:
            return None

        self.current_chat_id = chat_id
        return chat

    def save_current_chat(self):
        if self._is_saving:
            self._save_pending = True
            return False
        if not self.window:
            return False

        self._ensure_runtime_helpers()
        scene = self.window.chat_view.scene()
        if not has_saveable_nodes(scene) and not self._scene_has_content(scene):
            return False
        if not has_saveable_nodes(scene) and self._scene_has_content(scene) and not self.current_chat_id:
            self._show_status("Nothing was added to the chat canvas yet.")
            return False

        self._is_saving = True
        try:
            chat_data = self.serializer.serialize_chat_data()
        except Exception as error:
            self._is_saving = False
            self._show_status(f"Failed to prepare chat save payload: {error}")
            return False

        first_message = ""
        if not self.current_chat_id:
            last_message_node = next((node for node in reversed(scene.nodes) if node.text), None)
            first_message = last_message_node.text if last_message_node else "New Chat"

        self.save_thread = SaveWorkerThread(
            self.db,
            self.title_generator,
            chat_data,
            self.current_chat_id,
            first_message,
        )
        self.save_thread.finished.connect(self._on_save_finished)
        self.save_thread.error.connect(self._on_save_error)
        self.save_thread.cancelled.connect(self._on_save_cancelled)
        # Pin the epoch this save is being performed under, so its completion handler can
        # tell whether the user switched chats while it ran.
        self._saving_epoch = self._context_epoch
        try:
            self.save_thread.start()
        except Exception as error:
            self._is_saving = False
            self._show_status(f"Failed to start background save: {error}")
            if self.save_thread is not None:
                self.save_thread.deleteLater()
                self.save_thread = None
            return False
        return True

    def _on_save_finished(self, new_chat_id):
        thread = self.save_thread
        # Only adopt the saved chat as "active" if the user hasn't switched chats while
        # this save ran. If they did, the row we just wrote (the previous chat) is correct
        # and intact - we simply must not restore its id over the now-active chat, or the
        # next autosave would overwrite the wrong row. The current chat keeps its own id
        # (None for a brand-new chat -> it saves as its own INSERT next time).
        context_unchanged = self._saving_epoch == self._context_epoch
        if context_unchanged:
            self.current_chat_id = new_chat_id
        self._is_saving = False
        self.save_thread = None
        queued = self._save_pending
        self._save_pending = False
        if thread is not None:
            thread.deleteLater()
        if context_unchanged:
            print(f"Background save completed for chat ID: {new_chat_id}")
            if hasattr(self.window, "update_title_bar"):
                self.window.update_title_bar()
        else:
            print(
                f"Background save completed for a previous chat (id {new_chat_id}); "
                "active chat changed mid-save, so it is not restored as current."
            )
        # A queued save always targets the CURRENT scene + CURRENT chat id, both of which
        # are now consistent, so it is safe to run regardless of the epoch check above.
        if queued:
            self.save_current_chat()

    def _on_save_error(self, error_message):
        thread = self.save_thread
        self._is_saving = False
        queued = self._save_pending
        self._save_pending = False
        self.save_thread = None
        if thread is not None:
            thread.deleteLater()
        print(f"Error during background save: {error_message}")
        if hasattr(self.window, "notification_banner"):
            self.window.notification_banner.show_message(f"Error saving chat: {error_message}", 10000, "error")
        if queued:
            self.save_current_chat()

    def _on_save_cancelled(self):
        thread = self.save_thread
        self._is_saving = False
        queued = self._save_pending
        self._save_pending = False
        self.save_thread = None
        if thread is not None:
            thread.deleteLater()
        if queued:
            self.save_current_chat()

    def shutdown(self, timeout_ms=3000):
        thread = self.save_thread
        if thread is None:
            return True

        if thread.isRunning() and not thread.wait(timeout_ms):
            return False

        self.save_thread = None
        self._is_saving = False
        thread.deleteLater()
        return True
