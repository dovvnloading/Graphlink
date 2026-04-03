from PySide6.QtCore import QThread, Signal


class SaveWorkerThread(QThread):
    finished = Signal(int)
    error = Signal(str)

    def __init__(self, db, title_generator, chat_data, current_chat_id, first_message):
        super().__init__()
        self.db = db
        self.title_generator = title_generator
        self.chat_data = chat_data
        self.current_chat_id = current_chat_id
        self.first_message = first_message

    def run(self):
        try:
            new_chat_id = self.current_chat_id
            if not self.current_chat_id:
                title = self.title_generator.generate_title(self.first_message)
                new_chat_id = self.db.save_chat(title, self.chat_data)
            else:
                chat = self.db.load_chat(self.current_chat_id)
                if chat:
                    title = chat["title"]
                    self.db.update_chat(self.current_chat_id, title, self.chat_data)
                else:
                    title = self.title_generator.generate_title(self.first_message)
                    new_chat_id = self.db.save_chat(title, self.chat_data)

            if new_chat_id:
                self.db.save_notes(new_chat_id, self.chat_data.get("notes_data", []))
                self.db.save_pins(new_chat_id, self.chat_data.get("pins_data", []))

            self.finished.emit(new_chat_id)
        except Exception as exc:
            self.error.emit(f"Background save failed: {str(exc)}")
