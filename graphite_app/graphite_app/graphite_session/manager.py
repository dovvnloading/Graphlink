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
        self.serializer = SceneSerializer(window) if window else None
        self.deserializer = SceneDeserializer(window) if window else None

    def _ensure_runtime_helpers(self):
        if self.window and self.serializer is None:
            self.serializer = SceneSerializer(self.window)
        if self.window and self.deserializer is None:
            self.deserializer = SceneDeserializer(self.window)

    def load_chat(self, chat_id):
        chat = self.db.load_chat(chat_id)
        if not chat:
            return None

        if not self.window:
            self.current_chat_id = chat_id
            return chat

        self._ensure_runtime_helpers()
        notes_data = self.db.load_notes(chat_id)
        pins_data = self.db.load_pins(chat_id)
        loaded = self.deserializer.restore_chat(chat, notes_data, pins_data)
        self.current_chat_id = chat_id if loaded else None
        return chat

    def save_current_chat(self):
        if self._is_saving or not self.window:
            return

        self._ensure_runtime_helpers()
        scene = self.window.chat_view.scene()
        if not has_saveable_nodes(scene):
            return

        self._is_saving = True
        chat_data = self.serializer.serialize_chat_data()

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
        self.save_thread.start()

    def _on_save_finished(self, new_chat_id):
        self.current_chat_id = new_chat_id
        self._is_saving = False
        print(f"Background save completed for chat ID: {new_chat_id}")
        if hasattr(self.window, "update_title_bar"):
            self.window.update_title_bar()

    def _on_save_error(self, error_message):
        self._is_saving = False
        print(f"Error during background save: {error_message}")
        if hasattr(self.window, "notification_banner"):
            self.window.notification_banner.show_message(f"Error saving chat: {error_message}", 10000, "error")
