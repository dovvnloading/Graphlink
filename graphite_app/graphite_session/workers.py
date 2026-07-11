import threading
import re
from datetime import datetime

from PySide6.QtCore import QThread, Signal


class SaveWorkerThread(QThread):
    finished = Signal(int)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, db, title_generator, chat_data, current_chat_id, first_message):
        super().__init__()
        self.db = db
        self.title_generator = title_generator
        self.chat_data = chat_data
        self.current_chat_id = current_chat_id
        self.first_message = first_message
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _is_cancelled(self):
        return self._stop_event.is_set()

    def _fallback_title(self):
        # \w is unicode-aware by default for str patterns, so this keeps CJK/Cyrillic/
        # accented-Latin etc. first messages intact instead of stripping them down to
        # nothing (which used to force every non-ASCII chat to a timestamp title).
        words = re.findall(r"[\w']+", str(self.first_message or ""), re.UNICODE)
        if words:
            title = " ".join(words[:5]).strip()
            if title:
                return title[:80]
        return f"Chat {datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _generate_title(self):
        generate = getattr(self.title_generator, "generate_title", None)
        if callable(generate):
            try:
                title = str(generate(self.first_message) or "").strip()
                if title:
                    return title
            except Exception:
                pass
        return self._fallback_title()

    def run(self):
        try:
            if self._is_cancelled():
                self.cancelled.emit()
                return

            # Resolve (title, chat_id_for_save) first - chat_id_for_save is the id to
            # UPDATE, or None to INSERT a new chat - then make exactly one atomic call
            # that writes the chat blob, notes, and pins together (see
            # save_chat_atomically's docstring / doc/ARCHITECTURE_REVIEW_FINDINGS.md #52).
            chat_id_for_save = None
            if not self.current_chat_id:
                title = self._generate_title()
            else:
                chat = self.db.load_chat(self.current_chat_id)
                if self._is_cancelled():
                    self.cancelled.emit()
                    return
                if chat:
                    title = chat["title"]
                    chat_id_for_save = self.current_chat_id
                else:
                    title = self._generate_title()

            if self._is_cancelled():
                self.cancelled.emit()
                return

            new_chat_id = self.db.save_chat_atomically(
                chat_id_for_save,
                title,
                self.chat_data,
                self.chat_data.get("notes_data", []),
                self.chat_data.get("pins_data", []),
            )

            self.finished.emit(new_chat_id)
        except Exception as exc:
            if self._is_cancelled():
                self.cancelled.emit()
                return
            self.error.emit(f"Background save failed: {str(exc)}")
