from graphite_session.database import ChatDatabase
from graphite_session.deserializers import SceneDeserializer
from graphite_session.scene_index import has_saveable_nodes
from graphite_session.serializers import SceneSerializer
from graphite_session.title_generator import TitleGenerator
from graphite_session.workers import SaveWorkerThread


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

    def load_chat(self, chat_id):
        chat = self.db.load_chat(chat_id)
        if not chat:
            return None

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
        self.current_chat_id = new_chat_id
        self._is_saving = False
        self.save_thread = None
        queued = self._save_pending
        self._save_pending = False
        if thread is not None:
            thread.deleteLater()
        print(f"Background save completed for chat ID: {new_chat_id}")
        if hasattr(self.window, "update_title_bar"):
            self.window.update_title_bar()
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
