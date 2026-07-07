import json
import sqlite3
from pathlib import Path


class ChatDatabase:
    """Manage persisted chat sessions and side tables."""

    def __init__(self):
        self.db_path = Path.home() / ".graphlink" / "chats.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_database()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _prepare_chat_payload(self, chat_data):
        payload = dict(chat_data)
        payload.pop("notes_data", None)
        payload.pop("pins_data", None)
        return payload

    def init_database(self):
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    position_x REAL NOT NULL,
                    position_y REAL NOT NULL,
                    width REAL NOT NULL,
                    height REAL NOT NULL,
                    color TEXT NOT NULL,
                    header_color TEXT,
                    FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    note TEXT,
                    position_x REAL NOT NULL,
                    position_y REAL NOT NULL,
                    FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_chat_id ON notes(chat_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pins_chat_id ON pins(chat_id)")

            cursor.execute("PRAGMA table_info(notes)")
            columns = [info[1] for info in cursor.fetchall()]

            if "is_system_prompt" not in columns:
                try:
                    cursor.execute("ALTER TABLE notes ADD COLUMN is_system_prompt INTEGER DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError as exc:
                    print(f"Could not add column, it might already exist: {exc}")

            if "is_summary_note" not in columns:
                try:
                    cursor.execute("ALTER TABLE notes ADD COLUMN is_summary_note INTEGER DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError as exc:
                    print(f"Could not add column, it might already exist: {exc}")

    def save_pins(self, chat_id, pins_data):
        with self._connect() as conn:
            conn.execute("DELETE FROM pins WHERE chat_id = ?", (chat_id,))
            for pin_data in pins_data:
                conn.execute(
                    """
                    INSERT INTO pins (
                        chat_id, title, note, position_x, position_y
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        pin_data["title"],
                        pin_data["note"],
                        pin_data["position"]["x"],
                        pin_data["position"]["y"],
                    ),
                )

    def load_pins(self, chat_id):
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT title, note, position_x, position_y
                FROM pins WHERE chat_id = ?
                """,
                (chat_id,),
            )
            pins = []
            for row in cursor.fetchall():
                pins.append(
                    {
                        "title": row[0],
                        "note": row[1],
                        "position": {"x": row[2], "y": row[3]},
                    }
                )
            return pins

    def save_notes(self, chat_id, notes_data):
        with self._connect() as conn:
            conn.execute("DELETE FROM notes WHERE chat_id = ?", (chat_id,))
            for note_data in notes_data:
                conn.execute(
                    """
                    INSERT INTO notes (
                        chat_id, content, position_x, position_y,
                        width, height, color, header_color, is_system_prompt, is_summary_note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        note_data["content"],
                        note_data["position"]["x"],
                        note_data["position"]["y"],
                        note_data["size"]["width"],
                        note_data["size"]["height"],
                        note_data["color"],
                        note_data.get("header_color"),
                        1 if note_data.get("is_system_prompt") else 0,
                        1 if note_data.get("is_summary_note") else 0,
                    ),
                )

    def load_notes(self, chat_id):
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT content, position_x, position_y, width, height,
                       color, header_color, is_system_prompt, is_summary_note
                FROM notes WHERE chat_id = ?
                """,
                (chat_id,),
            )
            notes = []
            for row in cursor.fetchall():
                notes.append(
                    {
                        "content": row[0],
                        "position": {"x": row[1], "y": row[2]},
                        "size": {"width": row[3], "height": row[4]},
                        "color": row[5],
                        "header_color": row[6],
                        "is_system_prompt": bool(row[7]),
                        "is_summary_note": bool(row[8]),
                    }
                )
            return notes

    def save_chat(self, title, chat_data):
        payload = self._prepare_chat_payload(chat_data)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chats (title, data, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (title, json.dumps(payload)),
            )
            return cursor.lastrowid

    def get_latest_chat_id(self):
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT id FROM chats
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            result = cursor.fetchone()
            return result[0] if result else None

    def update_chat(self, chat_id, title, chat_data):
        payload = self._prepare_chat_payload(chat_data)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chats
                SET title = ?, data = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (title, json.dumps(payload), chat_id),
            )

    def load_chat(self, chat_id):
        with self._connect() as conn:
            result = conn.execute(
                """
                SELECT title, data FROM chats WHERE id = ?
                """,
                (chat_id,),
            ).fetchone()
            if result:
                return {
                    "title": result[0],
                    "data": json.loads(result[1]),
                }
            return None

    def get_all_chats(self):
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM chats
                ORDER BY updated_at DESC
                """
            ).fetchall()

    def delete_chat(self, chat_id):
        with self._connect() as conn:
            conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    def rename_chat(self, chat_id, new_title):
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chats
                SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_title, chat_id),
            )
