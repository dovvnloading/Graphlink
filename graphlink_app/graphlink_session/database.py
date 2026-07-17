import json
import sqlite3
from pathlib import Path
from uuid import uuid4


class ChatDatabase:
    """Manage persisted chat sessions and side tables."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path is not None else Path.home() / ".graphlink" / "chats.db"
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

            cursor.execute("PRAGMA table_info(pins)")
            pin_columns = [info[1] for info in cursor.fetchall()]
            added_sort_order = "sort_order" not in pin_columns
            if "pin_id" not in pin_columns:
                cursor.execute("ALTER TABLE pins ADD COLUMN pin_id TEXT")
            if added_sort_order:
                cursor.execute("ALTER TABLE pins ADD COLUMN sort_order INTEGER DEFAULT 0")
            if "anchor_item_id" not in pin_columns:
                cursor.execute("ALTER TABLE pins ADD COLUMN anchor_item_id TEXT")
            if "created_at" not in pin_columns:
                cursor.execute("ALTER TABLE pins ADD COLUMN created_at TEXT")

            # Older databases have no stable IDs or ordering. Preserve their row
            # order while assigning the new fields once, during initialization.
            cursor.execute("SELECT id, chat_id, pin_id, sort_order FROM pins ORDER BY chat_id, id")
            next_order_by_chat = {}
            seen_pin_ids = set()
            for row_id, chat_id, pin_id, sort_order in cursor.fetchall():
                order = next_order_by_chat.get(chat_id, 0)
                next_order_by_chat[chat_id] = order + 1
                candidate = str(pin_id or uuid4().hex)
                key = (chat_id, candidate)
                if key in seen_pin_ids:
                    candidate = uuid4().hex
                    key = (chat_id, candidate)
                seen_pin_ids.add(key)
                if added_sort_order or sort_order is None or candidate != str(pin_id or ""):
                    cursor.execute(
                        "UPDATE pins SET pin_id = ?, sort_order = ? WHERE id = ?",
                        (candidate, order, row_id),
                    )
                elif candidate != str(pin_id):
                    cursor.execute("UPDATE pins SET pin_id = ? WHERE id = ?", (candidate, row_id))

            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_pins_chat_pin_id ON pins(chat_id, pin_id)"
            )

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

    def _write_pins(self, conn, chat_id, pins_data):
        conn.execute("DELETE FROM pins WHERE chat_id = ?", (chat_id,))
        for index, pin_data in enumerate(pins_data):
            conn.execute(
                """
                INSERT INTO pins (
                    chat_id, title, note, position_x, position_y,
                    pin_id, sort_order, anchor_item_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    pin_data.get("title", "Canvas location"),
                    pin_data.get("note", ""),
                    pin_data["position"]["x"],
                    pin_data["position"]["y"],
                    pin_data.get("pin_id") or str(uuid4().hex),
                    pin_data.get("sort_order", index),
                    pin_data.get("anchor_item_id"),
                    pin_data.get("created_at"),
                ),
            )

    def save_pins(self, chat_id, pins_data):
        with self._connect() as conn:
            self._write_pins(conn, chat_id, pins_data)

    def load_pins(self, chat_id):
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT pin_id, title, note, position_x, position_y,
                       anchor_item_id, sort_order, created_at
                FROM pins WHERE chat_id = ?
                ORDER BY sort_order, id
                """,
                (chat_id,),
            )
            pins = []
            for index, row in enumerate(cursor.fetchall()):
                pins.append(
                    {
                        "pin_id": row[0],
                        "title": row[1],
                        "note": row[2],
                        "position": {"x": row[3], "y": row[4]},
                        "anchor_item_id": row[5],
                        "sort_order": row[6] if row[6] is not None else index,
                        "created_at": row[7],
                    }
                )
            return pins

    def _write_notes(self, conn, chat_id, notes_data):
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

    def save_notes(self, chat_id, notes_data):
        with self._connect() as conn:
            self._write_notes(conn, chat_id, notes_data)

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

    def save_chat_atomically(self, chat_id, title, chat_data, notes_data, pins_data):
        """Persist the chat blob, notes, and pins as a single transaction.

        save_chat/update_chat/save_notes/save_pins each previously opened their own
        connection and committed independently (see
        doc/ARCHITECTURE_REVIEW_FINDINGS.md #52) - a crash between any two of those
        steps left the chat row, notes, and pins inconsistent with each other. Here
        they share one connection, so Python's sqlite3 context manager commits all
        three together on success or rolls all three back together on any exception.

        Args:
            chat_id: Existing chat id to update, or None/falsy to insert a new chat.
            title: Chat title.
            chat_data: Scene payload (as passed to save_chat/update_chat).
            notes_data: As passed to save_notes.
            pins_data: As passed to save_pins.

        Returns:
            The chat id (chat_id if given, otherwise the newly inserted row's id).
        """
        payload = self._prepare_chat_payload(chat_data)
        with self._connect() as conn:
            if chat_id:
                conn.execute(
                    """
                    UPDATE chats
                    SET title = ?, data = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (title, json.dumps(payload), chat_id),
                )
                resolved_chat_id = chat_id
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO chats (title, data, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    """,
                    (title, json.dumps(payload)),
                )
                resolved_chat_id = cursor.lastrowid

            self._write_notes(conn, resolved_chat_id, notes_data)
            self._write_pins(conn, resolved_chat_id, pins_data)

            return resolved_chat_id

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

    def get_chat_title(self, chat_id):
        # Callers that only need the title (e.g. the window title bar) shouldn't pay
        # for reading and json.loads()-ing the full chat payload - see
        # doc/ARCHITECTURE_REVIEW_FINDINGS.md #45/#50 on how large that payload can get
        # (every node, connection, and base64-inlined image in the chat).
        with self._connect() as conn:
            result = conn.execute(
                "SELECT title FROM chats WHERE id = ?",
                (chat_id,),
            ).fetchone()
            return result[0] if result else None

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
