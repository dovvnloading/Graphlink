import json 
import sqlite3
from datetime import datetime
from pathlib import Path
import ollama
from PySide6.QtCore import QPointF, QRectF, QThread, Signal
from PySide6.QtGui import QTransform
import base64
import traceback
import logging

# Import UI classes needed for serialization/deserialization
from graphite_canvas_items import Note, NavigationPin, ChartItem, Frame, Container
from graphite_connections import (
    ConnectionItem, ContentConnectionItem, SystemPromptConnectionItem,
    DocumentConnectionItem, ImageConnectionItem, PyCoderConnectionItem,
    ConversationConnectionItem, ReasoningConnectionItem, GroupSummaryConnectionItem,
    HtmlConnectionItem, ThinkingConnectionItem
)
from graphite_node import ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode
from graphite_pycoder import PyCoderNode, PyCoderMode
from graphite_plugin_code_sandbox import CodeSandboxNode, CodeSandboxConnectionItem
from graphite_web import WebNode, WebConnectionItem
from graphite_conversation_node import ConversationNode
from graphite_reasoning import ReasoningNode
from graphite_html_view import HtmlViewNode
from graphite_plugin_artifact import ArtifactNode, ArtifactConnectionItem
from graphite_plugin_workflow import WorkflowNode, WorkflowConnectionItem
from graphite_plugin_graph_diff import GraphDiffNode, GraphDiffConnectionItem
from graphite_plugin_quality_gate import QualityGateNode, QualityGateConnectionItem
from graphite_plugin_code_review import CodeReviewNode, CodeReviewConnectionItem
from graphite_plugin_gitlink import GitlinkNode, GitlinkConnectionItem
import graphite_config as config
import api_provider

def _process_content_for_serialization(content):
    """
    Recursively finds and base64-encodes image bytes within a list of content parts.
    This prepares multi-modal content for JSON serialization.

    Args:
        content (list or any): The content to process. Expected to be a list for multi-modal messages.

    Returns:
        list or any: A new list with image data encoded, or the original content if not a list.
    """
    if isinstance(content, list):
        processed_parts = []
        for part in content:
            # Check for the specific image_bytes dictionary structure
            if isinstance(part, dict) and part.get('type') == 'image_bytes' and isinstance(part.get('data'), bytes):
                # Create a copy to avoid modifying the original in-memory object
                new_part = part.copy()
                # Encode the raw bytes into a base64 string
                new_part['data'] = base64.b64encode(part['data']).decode('utf-8')
                processed_parts.append(new_part)
            else:
                # Append non-image parts as-is
                processed_parts.append(part)
        return processed_parts
    return content

def _process_content_for_deserialization(content):
    """
    Recursively finds and base64-decodes image strings within a list of content parts.
    This reconstructs raw image bytes after loading from a JSON file.

    Args:
        content (list or any): The content to process.

    Returns:
        list or any: A new list with image data decoded back to bytes, or the original content.
    """
    if isinstance(content, list):
        processed_parts = []
        for part in content:
            # Look for the specific structure of an encoded image part
            if isinstance(part, dict) and part.get('type') == 'image_bytes' and isinstance(part.get('data'), str):
                new_part = part.copy()
                try:
                    # Decode the base64 string back into raw bytes
                    new_part['data'] = base64.b64decode(part['data'])
                    processed_parts.append(new_part)
                except (base64.binascii.Error, ValueError) as e:
                    # Handle case where data is malformed or corrupted, log and show placeholder
                    logging.exception("Failed to decode base64 image data during deserialization.")
                    processed_parts.append({'type': 'text', 'text': '[ERROR: Image Data Corrupted]'})
            else:
                # Append non-image parts as-is
                processed_parts.append(part)
        return processed_parts
    return content

class TitleGenerator:
    """An agent responsible for generating concise titles for new chat sessions."""
    def __init__(self):
        """Initializes the TitleGenerator with a specific system prompt."""
        self.system_prompt = """You are a title generation assistant. Your only job is to create short, 
        2-3 word titles based on conversation content. Rules:
        - ONLY output the title, nothing else
        - Keep it between 2-3 words
        - Use title case
        - Make it descriptive but concise
        - NO punctuation
        - NO explanations
        - NO additional text"""
        
    def generate_title(self, message):
        """
        Generates a 2-3 word title based on the first user message of a chat.

        Args:
            message (str): The text content of the message to use for title generation.

        Returns:
            str: A formatted title string, or a default timestamped title on failure.
        """
        try:
            title = ""
            if api_provider.USE_API_MODE:
                # Use the chat provider for API mode as it handles various endpoints correctly for simple tasks.
                messages = [
                    {'role': 'system', 'content': self.system_prompt},
                    {'role': 'user', 'content': f"Create a 2-3 word title for this message: {message}"}
                ]
                response = api_provider.chat(task=config.TASK_TITLE, messages=messages)
                title = response['message']['content'].strip()
            else:
                # For Ollama, use the more direct `generate` endpoint for non-chat tasks
                # to prevent conversational boilerplate and unwanted tags.
                model = config.OLLAMA_MODELS.get(config.TASK_TITLE)
                if not model:
                    raise ValueError(f"No Ollama model configured for task: {config.TASK_TITLE}")
                
                response = ollama.generate(
                    model=model,
                    system=self.system_prompt,
                    prompt=f"Create a 2-3 word title for this message: {message}"
                )
                title = response['response'].strip()

            # Clean up title to ensure it adheres to the length constraint
            title = ' '.join(title.split()[:3])
            return title
        except Exception as e:
            # Fallback to a timestamped title if the API call fails
            print(f"Title generation failed: {e}")
            return f"Chat {datetime.now().strftime('%Y%m%d_%H%M')}"

class ChatDatabase:
    """Manages the SQLite database for storing and retrieving all chat session data."""
    def __init__(self):
        """Initializes the database connection and ensures the schema is up to date."""
        self.db_path = Path.home() / '.graphlink' / 'chats.db'
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
        payload.pop('notes_data', None)
        payload.pop('pins_data', None)
        return payload
        
    def init_database(self):
        """
        Creates all necessary tables if they don't exist and performs schema migrations
        by adding new columns to existing tables for backward compatibility.
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            # Main table for storing chat sessions. 'data' column holds the JSON blob.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data TEXT NOT NULL
                )
            """)
            
            # Separate table for notes, linked by chat_id for efficient loading.
            cursor.execute("""
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
            """)
            
            # Separate table for navigation pins.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    note TEXT,
                    position_x REAL NOT NULL,
                    position_y REAL NOT NULL,
                    FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_chat_id ON notes(chat_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pins_chat_id ON pins(chat_id)")

            # --- Schema Migration Logic ---
            # This section ensures that databases created with older versions of the application
            # are updated with new columns without losing data.
            cursor.execute("PRAGMA table_info(notes)")
            columns = [info[1] for info in cursor.fetchall()]
            
            # Add a boolean column to identify system prompt notes
            if 'is_system_prompt' not in columns:
                try:
                    cursor.execute("ALTER TABLE notes ADD COLUMN is_system_prompt INTEGER DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError as e:
                    # This might happen in a race condition, but it's safe to ignore.
                    print(f"Could not add column, it might already exist: {e}")
            
            # Add a boolean column to identify group summary notes
            if 'is_summary_note' not in columns:
                try:
                    cursor.execute("ALTER TABLE notes ADD COLUMN is_summary_note INTEGER DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError as e:
                    print(f"Could not add column, it might already exist: {e}")
            
    def save_pins(self, chat_id, pins_data):
        """
        Saves all navigation pins for a given chat session.

        Args:
            chat_id (int): The ID of the chat session.
            pins_data (list[dict]): A list of serialized pin dictionaries.
        """
        with self._connect() as conn:
            # First delete existing pins for this chat to prevent duplicates
            conn.execute("DELETE FROM pins WHERE chat_id = ?", (chat_id,))
            
            # Insert new pins
            for pin_data in pins_data:
                conn.execute("""
                    INSERT INTO pins (
                        chat_id, title, note, position_x, position_y
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    chat_id,
                    pin_data['title'],
                    pin_data['note'],
                    pin_data['position']['x'],
                    pin_data['position']['y']
                ))
                
    def load_pins(self, chat_id):
        """
        Loads all navigation pins for a chat session.

        Args:
            chat_id (int): The ID of the chat session.

        Returns:
            list[dict]: A list of deserialized pin dictionaries.
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT title, note, position_x, position_y
                FROM pins WHERE chat_id = ?
            """, (chat_id,))
            
            pins = []
            for row in cursor.fetchall():
                pins.append({
                    'title': row[0],
                    'note': row[1],
                    'position': {'x': row[2], 'y': row[3]}
                })
            return pins
            
    def save_notes(self, chat_id, notes_data):
        """
        Saves all notes for a given chat session.

        Args:
            chat_id (int): The ID of the chat session.
            notes_data (list[dict]): A list of serialized note dictionaries.
        """
        with self._connect() as conn:
            # First delete existing notes for this chat
            conn.execute("DELETE FROM notes WHERE chat_id = ?", (chat_id,))
            
            # Insert new notes
            for note_data in notes_data:
                conn.execute("""
                    INSERT INTO notes (
                        chat_id, content, position_x, position_y,
                        width, height, color, header_color, is_system_prompt, is_summary_note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chat_id,
                    note_data['content'],
                    note_data['position']['x'],
                    note_data['position']['y'],
                    note_data['size']['width'],
                    note_data['size']['height'],
                    note_data['color'],
                    note_data.get('header_color'),
                    1 if note_data.get('is_system_prompt') else 0,
                    1 if note_data.get('is_summary_note') else 0
                ))
                
    def load_notes(self, chat_id):
        """
        Loads all notes for a chat session.

        Args:
            chat_id (int): The ID of the chat session.

        Returns:
            list[dict]: A list of deserialized note dictionaries.
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT content, position_x, position_y, width, height,
                       color, header_color, is_system_prompt, is_summary_note
                FROM notes WHERE chat_id = ?
            """, (chat_id,))
            
            notes = []
            for row in cursor.fetchall():
                notes.append({
                    'content': row[0],
                    'position': {'x': row[1], 'y': row[2]},
                    'size': {'width': row[3], 'height': row[4]},
                    'color': row[5],
                    'header_color': row[6],
                    'is_system_prompt': bool(row[7]),
                    'is_summary_note': bool(row[8])
                })
            return notes
            
    def save_chat(self, title, chat_data):
        """
        Saves a new chat session to the database.

        Args:
            title (str): The title of the chat.
            chat_data (dict): The serialized JSON data for the chat scene.

        Returns:
            int: The ID of the newly created chat record.
        """
        payload = self._prepare_chat_payload(chat_data)
        with self._connect() as conn:
            cursor = conn.execute("""
                INSERT INTO chats (title, data, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (title, json.dumps(payload)))
            return cursor.lastrowid
            
    def get_latest_chat_id(self):
        """
        Gets the ID of the most recently created chat.

        Returns:
            int or None: The ID of the latest chat, or None if no chats exist.
        """
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT id FROM chats 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            return result[0] if result else None
            
    def update_chat(self, chat_id, title, chat_data):
        """
        Updates an existing chat session in the database.

        Args:
            chat_id (int): The ID of the chat to update.
            title (str): The current title of the chat.
            chat_data (dict): The complete serialized JSON data for the chat scene.
        """
        payload = self._prepare_chat_payload(chat_data)
        with self._connect() as conn:
            conn.execute("""
                UPDATE chats 
                SET title = ?, data = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (title, json.dumps(payload), chat_id))
            
    def load_chat(self, chat_id):
        """
        Loads a specific chat session from the database.

        Args:
            chat_id (int): The ID of the chat to load.

        Returns:
            dict or None: A dictionary containing the chat 'title' and 'data', or None if not found.
        """
        with self._connect() as conn:
            result = conn.execute("""
                SELECT title, data FROM chats WHERE id = ?
            """, (chat_id,)).fetchone()
            if result:
                return {
                    'title': result[0],
                    'data': json.loads(result[1])
                }
            return None
            
    def get_all_chats(self):
        """
        Retrieves a list of all saved chats, ordered by most recently updated.

        Returns:
            list[tuple]: A list of tuples, each containing (id, title, created_at, updated_at).
        """
        with self._connect() as conn:
            return conn.execute("""
                SELECT id, title, created_at, updated_at 
                FROM chats 
                ORDER BY updated_at DESC
            """).fetchall()
            
    def delete_chat(self, chat_id):
        """
        Deletes a chat session from the database. Cascade delete handles associated notes/pins.

        Args:
            chat_id (int): The ID of the chat to delete.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            
    def rename_chat(self, chat_id, new_title):
        """
        Renames a chat session.

        Args:
            chat_id (int): The ID of the chat to rename.
            new_title (str): The new title for the chat.
        """
        with self._connect() as conn:
            conn.execute("""
                UPDATE chats 
                SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_title, chat_id))

class SaveWorkerThread(QThread):
    finished = Signal(int) # Emits new chat ID on success
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
                    title = chat['title']
                    self.db.update_chat(self.current_chat_id, title, self.chat_data)
                else:
                    title = self.title_generator.generate_title(self.first_message)
                    new_chat_id = self.db.save_chat(title, self.chat_data)
            
            if new_chat_id:
                self.db.save_notes(new_chat_id, self.chat_data.get('notes_data', []))
                self.db.save_pins(new_chat_id, self.chat_data.get('pins_data', []))
            
            self.finished.emit(new_chat_id)
        except Exception as e:
            self.error.emit(f"Background save failed: {str(e)}")


class ChatSessionManager:
    """
    Orchestrates the saving and loading of chat sessions. It acts as the bridge
    between the live QGraphicsScene (UI state) and the ChatDatabase (persistent storage),
    handling the complex logic of serialization and deserialization.
    """
    def __init__(self, window):
        """
        Initializes the ChatSessionManager.

        Args:
            window (QMainWindow): A reference to the main application window.
        """
        self.window = window
        self.db = ChatDatabase()
        self.title_generator = TitleGenerator()
        self.current_chat_id = None
        self.save_thread = None
        self._is_saving = False
        
    def serialize_pin(self, pin):
        return {
            'title': pin.title,
            'note': pin.note,
            'position': {'x': pin.pos().x(), 'y': pin.pos().y()}
        }
        
    def serialize_pin_layout(self, pin):
        return {
            'position': {'x': pin.pos().x(), 'y': pin.pos().y()}
        }
        
    def serialize_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
            'pins': [self.serialize_pin_layout(pin) for pin in connection.pins]
        }
    
    def serialize_content_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_document_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_image_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_thinking_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_system_prompt_connection(self, connection, notes_list, nodes_list):
        return {
            'start_note_index': notes_list.index(connection.start_node),
            'end_node_index': nodes_list.index(connection.end_node),
        }
    
    def serialize_pycoder_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_code_sandbox_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }
    
    def serialize_web_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_conversation_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }
        
    def serialize_reasoning_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_html_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_artifact_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_workflow_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_quality_gate_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_code_review_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_gitlink_connection(self, connection, all_nodes_list):
        return {
            'start_node_index': all_nodes_list.index(connection.start_node),
            'end_node_index': all_nodes_list.index(connection.end_node),
        }

    def serialize_group_summary_connection(self, connection, nodes_list, notes_list):
        return {
            'start_node_index': nodes_list.index(connection.start_node),
            'end_note_index': notes_list.index(connection.end_node),
        }
        
    def serialize_node(self, node):
        """
        Converts a generic node object into a serializable dictionary.
        """
        scene = self.window.chat_view.scene()
        all_nodes_list = (scene.nodes + scene.code_nodes + scene.document_nodes +
                          scene.image_nodes + scene.thinking_nodes + scene.pycoder_nodes + scene.code_sandbox_nodes + scene.web_nodes +
                          scene.conversation_nodes + scene.reasoning_nodes + scene.html_view_nodes +
                          scene.artifact_nodes + scene.workflow_nodes + scene.graph_diff_nodes + scene.quality_gate_nodes + scene.code_review_nodes +
                          scene.gitlink_nodes)

        if isinstance(node, ChatNode):
            serializable_history = []
            for msg in node.conversation_history:
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)
            return {
                'node_type': 'chat',
                'raw_content': _process_content_for_serialization(node.raw_content),
                'is_user': node.is_user,
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'conversation_history': serializable_history,
                'children_indices': [all_nodes_list.index(child) for child in node.children],
                'scroll_value': node.scroll_value,
                'is_collapsed': node.is_collapsed
            }
        elif isinstance(node, CodeNode):
            return {
                'node_type': 'code',
                'code': node.code,
                'language': node.language,
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'parent_content_node_index': all_nodes_list.index(node.parent_content_node)
            }
        elif isinstance(node, DocumentNode):
            return {
                'node_type': 'document',
                'title': node.title,
                'content': node.content,
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'parent_content_node_index': all_nodes_list.index(node.parent_content_node)
            }
        elif isinstance(node, ImageNode):
            return {
                'node_type': 'image',
                'image_bytes': base64.b64encode(node.image_bytes).decode('utf-8'),
                'prompt': node.prompt,
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'parent_content_node_index': all_nodes_list.index(node.parent_content_node)
            }
        elif isinstance(node, ThinkingNode):
            return {
                'node_type': 'thinking',
                'thinking_text': node.thinking_text,
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'parent_content_node_index': all_nodes_list.index(node.parent_content_node),
                'is_docked': node.is_docked
            }
        elif isinstance(node, PyCoderNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)
                
            return {
                'node_type': 'pycoder',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'mode': node.mode.name,
                'prompt': node.get_prompt(),
                'code': node.get_code(),
                'output': node.output_display.toPlainText(),
                'analysis': node.ai_analysis_display.toPlainText(),
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, CodeSandboxNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)

            return {
                'node_type': 'code_sandbox',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'prompt': node.get_prompt(),
                'requirements': node.get_requirements(),
                'code': node.get_code(),
                'output': node.output_display.toPlainText(),
                'analysis': node.ai_analysis_display.toPlainText(),
                'status': node.status,
                'sandbox_id': node.sandbox_id,
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, WebNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)
                
            return {
                'node_type': 'web',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'query': node.query,
                'status': node.status,
                'summary': node.summary,
                'sources': node.sources,
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, ConversationNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)
                
            return {
                'node_type': 'conversation',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, ReasoningNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)
                
            return {
                'node_type': 'reasoning',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'prompt': node.prompt,
                'thinking_budget': node.thinking_budget,
                'thought_process': node.thought_process,
                'status': node.status,
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, HtmlViewNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)
                
            return {
                'node_type': 'html',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'html_content': node.html_input.toHtml(),
                'splitter_state': node.get_splitter_state(),
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, ArtifactNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)
                
            serializable_local_history = []
            for msg in getattr(node, 'local_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_local_history.append(new_msg)

            return {
                'node_type': 'artifact',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'instruction': node.get_instruction(),
                'content': node.get_artifact_content(),
                'conversation_history': serializable_history,
                'local_history': serializable_local_history,
                'chat_html_cache': node.chat_html_cache,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, WorkflowNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)

            return {
                'node_type': 'workflow',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'goal': node.get_goal(),
                'constraints': node.get_constraints(),
                'status': node.status,
                'blueprint_markdown': node.blueprint_markdown,
                'recommendations': node.recommendations,
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, GraphDiffNode):
            return {
                'node_type': 'graph_diff',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'status': node.status,
                'comparison_markdown': node.comparison_markdown,
                'note_summary': node.note_summary,
                'left_source_index': all_nodes_list.index(node.left_source_node),
                'right_source_index': all_nodes_list.index(node.right_source_node),
                'is_collapsed': node.is_collapsed,
                'children_indices': []
            }
        elif isinstance(node, QualityGateNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)

            return {
                'node_type': 'quality_gate',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'goal': node.get_goal(),
                'criteria': node.get_criteria(),
                'status': node.status,
                'verdict': node.verdict,
                'readiness_score': node.readiness_score,
                'review_markdown': node.review_markdown,
                'note_summary': node.note_summary,
                'recommendations': node.recommendations,
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, CodeReviewNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)

            return {
                'node_type': 'code_review',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'review_context': node.get_review_context(),
                'source_text': node.source_editor.toPlainText(),
                'source_state': node.source_state,
                'status': node.status,
                'verdict': node.verdict,
                'quality_score': node.quality_score,
                'risk_level': node.risk_level,
                'review_markdown': node.review_markdown,
                'review_data': node.review_data,
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        elif isinstance(node, GitlinkNode):
            serializable_history = []
            for msg in getattr(node, 'conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_serialization(msg['content'])
                serializable_history.append(new_msg)

            return {
                'node_type': 'gitlink',
                'position': {'x': node.pos().x(), 'y': node.pos().y()},
                'task_prompt': node.get_task_prompt(),
                'repo_state': dict(getattr(node, 'repo_state', {}) or {}),
                'repo_file_paths': list(getattr(node, 'repo_file_paths', []) or []),
                'selected_paths': list(getattr(node, 'selected_paths', []) or []),
                'context_xml': getattr(node, 'context_xml', ''),
                'context_stats': dict(getattr(node, 'context_stats', {}) or {}),
                'proposal_data': dict(getattr(node, 'proposal_data', {}) or {}),
                'preview_text': getattr(node, 'preview_text', ''),
                'conversation_history': serializable_history,
                'is_collapsed': node.is_collapsed,
                'parent_node_index': all_nodes_list.index(node.parent_node),
                'children_indices': [all_nodes_list.index(child) for child in node.children]
            }
        return None

    def serialize_frame(self, frame):
        scene = self.window.chat_view.scene()
        all_nodes_list = (scene.nodes + scene.code_nodes + scene.document_nodes +
                          scene.image_nodes + scene.thinking_nodes + scene.pycoder_nodes + scene.code_sandbox_nodes + scene.web_nodes +
                          scene.conversation_nodes + scene.reasoning_nodes + scene.html_view_nodes +
                          scene.artifact_nodes + scene.workflow_nodes + scene.graph_diff_nodes + scene.quality_gate_nodes + scene.code_review_nodes +
                          scene.gitlink_nodes)
        return {
            'nodes': [all_nodes_list.index(node) for node in frame.nodes],
            'position': {'x': frame.pos().x(), 'y': frame.pos().y()},
            'note': frame.note,
            'size': {
                'width': frame.rect.width(),
                'height': frame.rect.height()
            },
            'is_locked': frame.is_locked,
            'is_collapsed': frame.is_collapsed,
            'color': frame.color,
            'header_color': frame.header_color
        }

    def serialize_container(self, container, all_items_map):
        return {
            'items': [all_items_map[item] for item in container.contained_items],
            'position': {'x': container.pos().x(), 'y': container.pos().y()},
            'title': container.title,
            'is_collapsed': container.is_collapsed,
            'color': container.color,
            'header_color': container.header_color,
            'expanded_rect': {
                'x': container.expanded_rect.x(),
                'y': container.expanded_rect.y(),
                'width': container.expanded_rect.width(),
                'height': container.expanded_rect.height()
            }
        }
        
    def serialize_note(self, note):
        return {
            'content': note.content,
            'position': {'x': note.pos().x(), 'y': note.pos().y()},
            'size': {'width': note.width, 'height': note.height},
            'color': note.color,
            'header_color': note.header_color,
            'is_system_prompt': getattr(note, 'is_system_prompt', False),
            'is_summary_note': getattr(note, 'is_summary_note', False)
        }
        
    def serialize_chart(self, chart, all_nodes_list):
        parent_node = getattr(chart, 'parent_content_node', None)
        parent_node_index = all_nodes_list.index(parent_node) if parent_node in all_nodes_list else None
        return {
            'data': chart.data,
            'position': {'x': chart.pos().x(), 'y': chart.pos().y()},
            'size': {'width': chart.width, 'height': chart.height},
            'parent_node_index': parent_node_index,
        }

    def _get_serialized_chat_data(self):
        scene = self.window.chat_view.scene()
    
        notes = [item for item in scene.items() if isinstance(item, Note)]
        pins = [item for item in scene.items() if isinstance(item, NavigationPin)]
        charts = list(scene.chart_nodes)
    
        all_nodes_list = (scene.nodes + scene.code_nodes + scene.document_nodes +
                          scene.image_nodes + scene.thinking_nodes + scene.pycoder_nodes + scene.code_sandbox_nodes + scene.web_nodes +
                          scene.conversation_nodes + scene.reasoning_nodes + scene.html_view_nodes +
                          scene.artifact_nodes + scene.workflow_nodes + scene.graph_diff_nodes + scene.quality_gate_nodes + scene.code_review_nodes +
                          scene.gitlink_nodes)
        
        all_serializable_items = all_nodes_list + notes + charts + scene.frames + scene.containers
        all_items_map = {item: i for i, item in enumerate(all_serializable_items)}

        chat_data = {
            'nodes': [self.serialize_node(node) for node in all_nodes_list],
            'connections': [self.serialize_connection(conn, all_nodes_list) for conn in scene.connections],
            'content_connections': [self.serialize_content_connection(conn, all_nodes_list) for conn in scene.content_connections],
            'document_connections': [self.serialize_document_connection(conn, all_nodes_list) for conn in scene.document_connections],
            'image_connections': [self.serialize_image_connection(conn, all_nodes_list) for conn in scene.image_connections],
            'thinking_connections': [self.serialize_thinking_connection(conn, all_nodes_list) for conn in scene.thinking_connections],
            'system_prompt_connections': [self.serialize_system_prompt_connection(conn, notes, scene.nodes) for conn in scene.system_prompt_connections],
            'pycoder_connections': [self.serialize_pycoder_connection(conn, all_nodes_list) for conn in scene.pycoder_connections],
            'code_sandbox_connections': [self.serialize_code_sandbox_connection(conn, all_nodes_list) for conn in scene.code_sandbox_connections],
            'web_connections': [self.serialize_web_connection(conn, all_nodes_list) for conn in scene.web_connections],
            'conversation_connections': [self.serialize_conversation_connection(conn, all_nodes_list) for conn in scene.conversation_connections],
            'reasoning_connections': [self.serialize_reasoning_connection(conn, all_nodes_list) for conn in scene.reasoning_connections],
            'group_summary_connections': [self.serialize_group_summary_connection(conn, scene.nodes, notes) for conn in scene.group_summary_connections],
            'html_connections': [self.serialize_html_connection(conn, all_nodes_list) for conn in scene.html_connections],
            'artifact_connections': [self.serialize_artifact_connection(conn, all_nodes_list) for conn in scene.artifact_connections],
            'workflow_connections': [self.serialize_workflow_connection(conn, all_nodes_list) for conn in scene.workflow_connections],
            'quality_gate_connections': [self.serialize_quality_gate_connection(conn, all_nodes_list) for conn in scene.quality_gate_connections],
            'code_review_connections': [self.serialize_code_review_connection(conn, all_nodes_list) for conn in scene.code_review_connections],
            'gitlink_connections': [self.serialize_gitlink_connection(conn, all_nodes_list) for conn in scene.gitlink_connections],
            'frames': [self.serialize_frame(frame) for frame in scene.frames],
            'containers': [self.serialize_container(c, all_items_map) for c in scene.containers],
            'charts': [self.serialize_chart(chart, all_nodes_list) for chart in charts],
            'total_session_tokens': self.window.total_session_tokens,
            'view_state': {
                'zoom_factor': self.window.chat_view._zoom_factor,
                'scroll_position': {
                    'x': self.window.chat_view.horizontalScrollBar().value(),
                    'y': self.window.chat_view.verticalScrollBar().value()
                }
            },
            'notes_data': [self.serialize_note(note) for note in notes],
            'pins_data': [self.serialize_pin(pin) for pin in pins]
        }
        return chat_data

    def deserialize_chart(self, data, scene, all_nodes_map):
        parent_node = all_nodes_map.get(data.get('parent_node_index'))
        chart = scene.add_chart(data['data'], QPointF(
            data['position']['x'],
            data['position']['y']
        ), parent_content_node=parent_node)
        
        if 'size' in data:
            chart.width = data['size']['width']
            chart.height = data['size']['height']
            chart.generate_chart()
            
        return chart
        
    def deserialize_pin(self, data, connection):
        pin = connection.add_pin(QPointF(0, 0))
        pin.setPos(data['position']['x'], data['position']['y'])
        return pin
        
    def deserialize_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map[data['start_node_index']]
        end_node = all_nodes_map[data['end_node_index']]
        
        connection = ConnectionItem(start_node, end_node)
        
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        
        scene.addItem(connection)
        scene.connections.append(connection)
        
        for pin_data in data.get('pins', []):
            self.deserialize_pin(pin_data, connection)
            
        return connection
        
    def deserialize_content_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map[data['start_node_index']]
        end_node = all_nodes_map[data['end_node_index']]
        connection = ContentConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.content_connections.append(connection)
        return connection

    def deserialize_document_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map[data['start_node_index']]
        end_node = all_nodes_map[data['end_node_index']]
        connection = DocumentConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.document_connections.append(connection)
        return connection

    def deserialize_image_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map[data['start_node_index']]
        end_node = all_nodes_map[data['end_node_index']]
        connection = ImageConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.image_connections.append(connection)
        return connection

    def deserialize_thinking_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map[data['start_node_index']]
        end_node = all_nodes_map[data['end_node_index']]
        connection = ThinkingConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.thinking_connections.append(connection)
        return connection

    def deserialize_system_prompt_connection(self, data, scene, notes_map, nodes_map):
        start_note = notes_map.get(data['start_note_index'])
        end_node = nodes_map.get(data['end_node_index'])
        
        if not start_note or not end_node:
            print(f"Warning: Skipping orphaned system prompt connection during load.")
            return None

        connection = SystemPromptConnectionItem(start_note, end_node)
        scene.addItem(connection)
        scene.system_prompt_connections.append(connection)
        return connection

    def deserialize_pycoder_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = PyCoderConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.pycoder_connections.append(connection)
        return connection

    def deserialize_code_sandbox_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = CodeSandboxConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.code_sandbox_connections.append(connection)
        return connection

    def deserialize_web_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = WebConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.web_connections.append(connection)
        return connection

    def deserialize_conversation_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = ConversationConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.conversation_connections.append(connection)
        return connection

    def deserialize_reasoning_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = ReasoningConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.reasoning_connections.append(connection)
        return connection

    def deserialize_html_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = HtmlConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.html_connections.append(connection)
        return connection

    def deserialize_artifact_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = ArtifactConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.artifact_connections.append(connection)
        return connection

    def deserialize_workflow_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = WorkflowConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.workflow_connections.append(connection)
        return connection

    def deserialize_quality_gate_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = QualityGateConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.quality_gate_connections.append(connection)
        return connection

    def deserialize_code_review_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = CodeReviewConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.code_review_connections.append(connection)
        return connection

    def deserialize_gitlink_connection(self, data, scene, all_nodes_map):
        start_node = all_nodes_map.get(data['start_node_index'])
        end_node = all_nodes_map.get(data['end_node_index'])
        if not start_node or not end_node:
            return None
        connection = GitlinkConnectionItem(start_node, end_node)
        if hasattr(end_node, 'incoming_connection'):
            end_node.incoming_connection = connection
        scene.addItem(connection)
        scene.gitlink_connections.append(connection)
        return connection

    def deserialize_group_summary_connection(self, data, scene, nodes_map, notes_map):
        start_node = nodes_map.get(data['start_node_index'])
        end_note = notes_map.get(data['end_note_index'])
        
        if not start_node or not end_note:
            print(f"Warning: Skipping orphaned group summary connection.")
            return None

        connection = GroupSummaryConnectionItem(start_node, end_note)
        scene.addItem(connection)
        scene.group_summary_connections.append(connection)
        return connection

    def deserialize_node(self, index, data, all_nodes_map):
        scene = self.window.chat_view.scene()
        node_type = data.get('node_type', 'chat')

        node = None
        if node_type == 'chat':
            raw_content = _process_content_for_deserialization(data.get('raw_content', data.get('text')))
            deserialized_history = []
            for msg in data.get('conversation_history', []):
                new_msg = msg.copy()
                new_msg['content'] = _process_content_for_deserialization(msg['content'])
                deserialized_history.append(new_msg)

            node = scene.add_chat_node(
                raw_content,
                is_user=data.get('is_user', True),
                parent_node=None,
                conversation_history=deserialized_history
            )
            node.setPos(data['position']['x'], data['position']['y'])
            node.scroll_value = data.get('scroll_value', 0)
            node.scrollbar.set_value(node.scroll_value)
            if data.get('is_collapsed', False):
                node.set_collapsed(True)

        elif node_type == 'code':
            parent_node = all_nodes_map.get(data['parent_content_node_index'])
            if parent_node:
                node = scene.add_code_node(
                    data['code'],
                    data['language'],
                    parent_node
                )
                node.setPos(data['position']['x'], data['position']['y'])
        
        elif node_type == 'document':
            parent_node = all_nodes_map.get(data['parent_content_node_index'])
            if parent_node:
                node = scene.add_document_node(
                    data['title'],
                    data['content'],
                    parent_node
                )
                node.setPos(data['position']['x'], data['position']['y'])

        elif node_type == 'image':
            parent_node = all_nodes_map.get(data['parent_content_node_index'])
            if parent_node:
                image_bytes = base64.b64decode(data['image_bytes'])
                node = scene.add_image_node(
                    image_bytes,
                    parent_node,
                    prompt=data.get('prompt', '')
                )
                node.setPos(data['position']['x'], data['position']['y'])
        
        elif node_type == 'thinking':
            parent_node = all_nodes_map.get(data['parent_content_node_index'])
            if parent_node:
                node = scene.add_thinking_node(
                    data['thinking_text'],
                    parent_node
                )
                node.setPos(data['position']['x'], data['position']['y'])
                if data.get('is_docked', False):
                    node.dock()
        
        elif node_type == 'pycoder':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                mode_name = data.get('mode', 'AI_DRIVEN')
                mode = PyCoderMode[mode_name]
                
                node = PyCoderNode(parent_node, mode=mode)
                node.setPos(data['position']['x'], data['position']['y'])
                
                node.prompt_input.setText(data.get('prompt', ''))
                node.set_code(data.get('code', '')) 
                node.set_output(data.get('output', ''))
                node.set_ai_analysis(data.get('analysis', ''))

                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)
                
                scene.addItem(node)
                scene.pycoder_nodes.append(node)

        elif node_type == 'code_sandbox':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = CodeSandboxNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                node.prompt_input.setPlainText(data.get('prompt', ''))
                node.set_requirements(data.get('requirements', ''))
                node.set_code(data.get('code', ''))
                node.set_output(data.get('output', ''))
                node.set_ai_analysis(data.get('analysis', ''))
                node.status = data.get('status', 'Idle')
                node.sandbox_id = data.get('sandbox_id', node.sandbox_id)
                tone = "success" if node.status == "Ready" else ("error" if node.status == "Error" else "info")
                node._update_status_pill(tone)

                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)

                node.sandbox_requested.connect(self.window.execute_code_sandbox_node)

                scene.addItem(node)
                scene.code_sandbox_nodes.append(node)
        
        elif node_type == 'web':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = WebNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                
                node.query_input.setText(data.get('query', ''))
                node.set_status(data.get('status', 'Idle'))
                summary = data.get('summary', '')
                sources = data.get('sources', [])
                if summary:
                    node.set_result(summary, sources)
                
                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                node.run_clicked.connect(self.window.execute_web_node)

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)
                
                scene.addItem(node)
                scene.web_nodes.append(node)
        
        elif node_type == 'conversation':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = ConversationNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                node.set_history(data.get('conversation_history', []))
                
                node.ai_request_sent.connect(self.window.handle_conversation_node_request)

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)
                
                scene.addItem(node)
                scene.conversation_nodes.append(node)
        
        elif node_type == 'reasoning':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = ReasoningNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                
                node.prompt_input.setText(data.get('prompt', ''))
                node.budget_slider.setValue(data.get('thinking_budget', 3))
                node.thought_process_display.setMarkdown(data.get('thought_process', ''))
                node.set_status(data.get('status', 'Idle'))
                
                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                node.reasoning_requested.connect(self.window.execute_reasoning_node)

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)
                
                scene.addItem(node)
                scene.reasoning_nodes.append(node)
        
        elif node_type == 'html':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = HtmlViewNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                node.set_html_content(data.get('html_content', ''))
                node.set_splitter_state(data.get('splitter_state'))

                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)
                
                scene.addItem(node)
                scene.html_view_nodes.append(node)

        elif node_type == 'artifact':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = ArtifactNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                
                node.instruction_input.setPlainText(data.get('instruction', ''))
                node.set_artifact_content(data.get('content', ''))
                
                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                deserialized_local_history = []
                for msg in data.get('local_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_local_history.append(new_msg)
                node.local_history = deserialized_local_history
                
                node.chat_html_cache = data.get('chat_html_cache', '')
                node.chat_display.setHtml(node.chat_html_cache)
                
                if data.get('is_collapsed', False):
                    node.set_collapsed(True)
                
                node.artifact_requested.connect(self.window.execute_artifact_node)
                
                scene.addItem(node)
                scene.artifact_nodes.append(node)

        elif node_type == 'workflow':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = WorkflowNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                node.goal_input.setPlainText(data.get('goal', ''))
                node.constraints_input.setPlainText(data.get('constraints', ''))

                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                node.blueprint_markdown = data.get('blueprint_markdown', '')
                node.recommendations = data.get('recommendations', [])
                node.status = data.get('status', 'Idle')
                if node.blueprint_markdown or node.recommendations:
                    node.set_plan({
                        'blueprint_markdown': node.blueprint_markdown,
                        'recommended_plugins': node.recommendations,
                    })
                else:
                    node.set_status(node.status)

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)

                node.workflow_requested.connect(self.window.execute_workflow_node)
                node.plugin_requested.connect(self.window.instantiate_seeded_plugin)

                scene.addItem(node)
                scene.workflow_nodes.append(node)

        elif node_type == 'graph_diff':
            left_source = all_nodes_map.get(data.get('left_source_index'))
            right_source = all_nodes_map.get(data.get('right_source_index'))
            if left_source and right_source:
                node = GraphDiffNode(left_source, right_source)
                node.setPos(data['position']['x'], data['position']['y'])
                node.comparison_markdown = data.get('comparison_markdown', '')
                node.note_summary = data.get('note_summary', '')
                node.status = data.get('status', 'Idle')

                if node.comparison_markdown:
                    node.set_result({
                        'comparison_markdown': node.comparison_markdown,
                        'note_summary': node.note_summary,
                    })
                else:
                    node.set_status(node.status)

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)

                node.compare_requested.connect(self.window.execute_graph_diff_node)
                node.note_requested.connect(self.window.create_graph_diff_note)

                scene.addItem(node)
                scene.graph_diff_nodes.append(node)

                for source_node in (left_source, right_source):
                    connection = GraphDiffConnectionItem(source_node, node)
                    scene.addItem(connection)
                    scene.graph_diff_connections.append(connection)

        elif node_type == 'quality_gate':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = QualityGateNode(parent_node)
                node.setPos(data['position']['x'], data['position']['y'])
                node.goal_input.setPlainText(data.get('goal', ''))
                node.criteria_input.setPlainText(data.get('criteria', ''))

                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                node.review_markdown = data.get('review_markdown', '')
                node.note_summary = data.get('note_summary', '')
                node.recommendations = data.get('recommendations', [])
                node.verdict = data.get('verdict', 'pending')
                node.readiness_score = data.get('readiness_score', 0)
                node.status = data.get('status', 'Idle')

                if node.review_markdown or node.recommendations:
                    node.set_review({
                        'verdict': node.verdict,
                        'readiness_score': node.readiness_score,
                        'review_markdown': node.review_markdown,
                        'note_summary': node.note_summary,
                        'recommended_plugins': node.recommendations,
                    })
                else:
                    node.set_status(node.status)

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)

                node.review_requested.connect(self.window.execute_quality_gate_node)
                node.plugin_requested.connect(self.window.instantiate_seeded_plugin)
                node.note_requested.connect(self.window.create_quality_gate_note)

                scene.addItem(node)
                scene.quality_gate_nodes.append(node)

        elif node_type == 'code_review':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = CodeReviewNode(parent_node, settings_manager=getattr(self.window, 'settings_manager', None))
                node.setPos(data['position']['x'], data['position']['y'])
                node.context_input.setPlainText(data.get('review_context', ''))
                node._set_source_text(data.get('source_text', ''), data.get('source_state', {
                    'origin': '',
                    'label': '',
                    'repo': '',
                    'branch': '',
                    'path': '',
                    'local_path': '',
                    'edited': False,
                }))

                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                node.review_markdown = data.get('review_markdown', '')
                node.review_data = data.get('review_data', {})
                node.verdict = data.get('verdict', 'pending')
                node.quality_score = data.get('quality_score', 0)
                node.risk_level = data.get('risk_level', 'unknown')
                node.status = data.get('status', 'Idle')

                if node.review_data:
                    node.set_review(node.review_data)
                elif node.review_markdown:
                    node.overview_display.setMarkdown(node.review_markdown)
                    node.set_status(node.status)
                else:
                    node.set_status(node.status)

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)

                node.review_requested.connect(self.window.execute_code_review_node)

                scene.addItem(node)
                scene.code_review_nodes.append(node)

        elif node_type == 'gitlink':
            parent_node = all_nodes_map.get(data['parent_node_index'])
            if parent_node:
                node = GitlinkNode(parent_node, settings_manager=getattr(self.window, 'settings_manager', None))
                node.setPos(data['position']['x'], data['position']['y'])

                deserialized_history = []
                for msg in data.get('conversation_history', []):
                    new_msg = msg.copy()
                    new_msg['content'] = _process_content_for_deserialization(msg['content'])
                    deserialized_history.append(new_msg)
                node.conversation_history = deserialized_history

                node.restore_saved_state(
                    repo_state=data.get('repo_state', {}),
                    repo_file_paths=data.get('repo_file_paths', []),
                    selected_paths=data.get('selected_paths', []),
                    task_prompt=data.get('task_prompt', ''),
                    context_xml=data.get('context_xml', ''),
                    context_stats=data.get('context_stats', {}),
                    proposal_data=data.get('proposal_data', {}),
                    preview_text=data.get('preview_text', ''),
                )

                if data.get('is_collapsed', False):
                    node.set_collapsed(True)

                node.gitlink_requested.connect(self.window.execute_gitlink_node)

                scene.addItem(node)
                scene.gitlink_nodes.append(node)

        if node:
            all_nodes_map[index] = node
        return node
        
    def deserialize_frame(self, data, scene, all_nodes_map):
        nodes_indices = [i for i in data['nodes'] if i in all_nodes_map]
        nodes = [all_nodes_map[i] for i in nodes_indices]
        
        frame = Frame(nodes)
        frame.setPos(data['position']['x'], data['position']['y'])
        frame.note = data['note']
        
        if 'color' in data:
            frame.color = data['color']
        if 'header_color' in data:
            frame.header_color = data['header_color']
            
        if 'size' in data:
            frame.rect.setWidth(data['size']['width'])
            frame.rect.setHeight(data['size']['height'])
            
        scene.addItem(frame)
        scene.frames.append(frame)
        frame.setZValue(-2) 
        if not data.get('is_locked', True):
            frame.toggle_lock()
        if data.get('is_collapsed', False):
            frame.toggle_collapse()
        return frame

    def deserialize_container(self, data, scene, all_items_map):
        items_indices = [i for i in data['items'] if i in all_items_map]
        items = [all_items_map[i] for i in items_indices]
        container = Container(items)
        container.setPos(data['position']['x'], data['position']['y'])
        container.title = data.get('title', "Container")
        container.color = data.get('color', "#3a3a3a")
        container.header_color = data.get('header_color')
        
        rect_data = data.get('expanded_rect')
        if rect_data:
            container.expanded_rect = QRectF(rect_data['x'], rect_data['y'], rect_data['width'], rect_data['height'])

        if data.get('is_collapsed', False):
            container.toggle_collapse()

        scene.addItem(container)
        scene.containers.append(container)
        container.setZValue(-3)
        return container

    def load_chat(self, chat_id):
        chat = self.db.load_chat(chat_id)
        if not chat:
            return

        scene = self.window.chat_view.scene()
        scene.clear()
        self.window.current_node = None

        try:
            all_nodes_map = {}
            notes_map = {}
            
            for i, node_data in enumerate(chat['data']['nodes']):
                self.deserialize_node(i, node_data, all_nodes_map)

            for i, node_data in enumerate(chat['data']['nodes']):
                node = all_nodes_map.get(i)
                if not node: continue
                
                valid_node_types = (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, ArtifactNode, WorkflowNode, GraphDiffNode, QualityGateNode, CodeReviewNode, GitlinkNode)
                if isinstance(node, valid_node_types) and 'children_indices' in node_data:
                    for child_index in node_data['children_indices']:
                        child_node = all_nodes_map.get(child_index)
                        if child_node:
                            node.children.append(child_node)
                            child_node.parent_node = node

            notes_data = self.db.load_notes(chat_id)
            for i, note_data in enumerate(notes_data):
                note = scene.add_note(QPointF(note_data['position']['x'], note_data['position']['y']))
                note.width = note_data['size']['width']
                note.color = note_data['color']
                note.header_color = note_data['header_color']
                note.is_system_prompt = note_data.get('is_system_prompt', False)
                note.is_summary_note = note_data.get('is_summary_note', False)
                note.content = note_data['content']
                notes_map[i] = note

            charts_map = {}
            if 'charts' in chat['data']:
                for i, chart_data in enumerate(chat['data']['charts']):
                    charts_map[i] = self.deserialize_chart(chart_data, scene, all_nodes_map)

            all_deserialized_nodes = list(all_nodes_map.values())
            all_deserialized_notes = list(notes_map.values())
            all_deserialized_charts = list(charts_map.values())

            frames_map = {}
            if 'frames' in chat['data']:
                for i, frame_data in enumerate(chat['data']['frames']):
                    frames_map[i] = self.deserialize_frame(frame_data, scene, all_nodes_map)

            all_deserialized_frames = list(frames_map.values())
            
            all_items_list = all_deserialized_nodes + all_deserialized_notes + all_deserialized_charts + all_deserialized_frames
            all_items_map = {i: item for i, item in enumerate(all_items_list)}

            if 'containers' in chat['data']:
                for container_data in chat['data']['containers']:
                    self.deserialize_container(container_data, scene, all_items_map)

            chat_nodes_map = {i: node for i, node in enumerate(scene.nodes)}

            for conn_data in chat['data'].get('connections', []):
                self.deserialize_connection(conn_data, scene, all_nodes_map)
            
            for conn_data in chat['data'].get('content_connections', []):
                self.deserialize_content_connection(conn_data, scene, all_nodes_map)

            for conn_data in chat['data'].get('document_connections', []):
                self.deserialize_document_connection(conn_data, scene, all_nodes_map)

            for conn_data in chat['data'].get('image_connections', []):
                self.deserialize_image_connection(conn_data, scene, all_nodes_map)
            
            if 'thinking_connections' in chat['data']:
                for conn_data in chat['data']['thinking_connections']:
                    self.deserialize_thinking_connection(conn_data, scene, all_nodes_map)
            
            for conn_data in chat['data'].get('pycoder_connections', []):
                self.deserialize_pycoder_connection(conn_data, scene, all_nodes_map)

            for conn_data in chat['data'].get('code_sandbox_connections', []):
                self.deserialize_code_sandbox_connection(conn_data, scene, all_nodes_map)
            
            if 'web_connections' in chat['data']:
                 for conn_data in chat['data']['web_connections']:
                    self.deserialize_web_connection(conn_data, scene, all_nodes_map)

            if 'conversation_connections' in chat['data']:
                for conn_data in chat['data']['conversation_connections']:
                    self.deserialize_conversation_connection(conn_data, scene, all_nodes_map)
            
            if 'reasoning_connections' in chat['data']:
                for conn_data in chat['data']['reasoning_connections']:
                    self.deserialize_reasoning_connection(conn_data, scene, all_nodes_map)
            
            if 'html_connections' in chat['data']:
                for conn_data in chat['data']['html_connections']:
                    self.deserialize_html_connection(conn_data, scene, all_nodes_map)
                    
            if 'artifact_connections' in chat['data']:
                for conn_data in chat['data']['artifact_connections']:
                    self.deserialize_artifact_connection(conn_data, scene, all_nodes_map)

            if 'workflow_connections' in chat['data']:
                for conn_data in chat['data']['workflow_connections']:
                    self.deserialize_workflow_connection(conn_data, scene, all_nodes_map)

            if 'quality_gate_connections' in chat['data']:
                for conn_data in chat['data']['quality_gate_connections']:
                    self.deserialize_quality_gate_connection(conn_data, scene, all_nodes_map)

            if 'code_review_connections' in chat['data']:
                for conn_data in chat['data']['code_review_connections']:
                    self.deserialize_code_review_connection(conn_data, scene, all_nodes_map)

            if 'gitlink_connections' in chat['data']:
                for conn_data in chat['data']['gitlink_connections']:
                    self.deserialize_gitlink_connection(conn_data, scene, all_nodes_map)

            if 'system_prompt_connections' in chat['data']:
                for conn_data in chat['data']['system_prompt_connections']:
                    self.deserialize_system_prompt_connection(conn_data, scene, notes_map, chat_nodes_map)
            
            if 'group_summary_connections' in chat['data']:
                for conn_data in chat['data']['group_summary_connections']:
                    self.deserialize_group_summary_connection(conn_data, scene, chat_nodes_map, notes_map)

            if self.window and hasattr(self.window, 'pin_overlay'):
                self.window.pin_overlay.clear_pins()
        
            pins_data = self.db.load_pins(chat_id)
            for pin_data in pins_data:
                pin = scene.add_navigation_pin(QPointF(pin_data['position']['x'], pin_data['position']['y']))
                pin.title, pin.note = pin_data['title'], pin_data.get('note', '')
                if self.window and hasattr(self.window, 'pin_overlay'):
                    self.window.pin_overlay.add_pin_button(pin)

            if 'view_state' in chat['data']:
                view_state = chat['data']['view_state']
                self.window.chat_view._zoom_factor = view_state['zoom_factor']
                self.window.chat_view.setTransform(QTransform().scale(view_state['zoom_factor'], view_state['zoom_factor']))
                self.window.chat_view.horizontalScrollBar().setValue(view_state['scroll_position']['x'])
                self.window.chat_view.verticalScrollBar().setValue(view_state['scroll_position']['y'])
    
            self.current_chat_id = chat_id
            scene.update_connections()
            
            if self.window:
                total_tokens = chat['data'].get('total_session_tokens', 0)
                self.window.reset_token_counter(total_tokens=total_tokens)

        except Exception as e:
            import traceback
            print(f"Error loading chat: {str(e)}")
            traceback.print_exc()
            if self.window and hasattr(self.window, 'notification_banner'):
                self.window.notification_banner.show_message(f"Failed to load the chat session. It may be corrupted.\nError: {e}", 8000, "error")
            
            scene.clear()
            self.current_chat_id = None
            if self.window:
                self.window.current_node = None
                self.window.message_input.setPlaceholderText("Type your message...")
                self.window.update_title_bar()
                self.window.reset_token_counter()
                if hasattr(self.window, 'pin_overlay') and self.window.pin_overlay:
                    self.window.pin_overlay.clear_pins()
            
        return chat
        
    def save_current_chat(self):
        if self._is_saving:
            return

        scene = self.window.chat_view.scene()
        if (not scene.nodes and not scene.conversation_nodes and not scene.reasoning_nodes and
                not scene.artifact_nodes and not scene.workflow_nodes and not scene.pycoder_nodes and not scene.code_sandbox_nodes and
                not scene.web_nodes and not scene.html_view_nodes and not scene.quality_gate_nodes and not scene.code_review_nodes and
                not scene.graph_diff_nodes and not scene.gitlink_nodes):
            return

        self._is_saving = True
        chat_data = self._get_serialized_chat_data()
        
        first_message = ""
        if not self.current_chat_id:
            last_message_node = next((node for node in reversed(scene.nodes) if node.text), None)
            first_message = last_message_node.text if last_message_node else "New Chat"

        self.save_thread = SaveWorkerThread(self.db, self.title_generator, chat_data, self.current_chat_id, first_message)
        self.save_thread.finished.connect(self._on_save_finished)
        self.save_thread.error.connect(self._on_save_error)
        self.save_thread.start()

    def _on_save_finished(self, new_chat_id):
        self.current_chat_id = new_chat_id
        self._is_saving = False
        print(f"Background save completed for chat ID: {new_chat_id}")
        if hasattr(self.window, 'update_title_bar'):
            self.window.update_title_bar()

    def _on_save_error(self, error_message):
        self._is_saving = False
        print(f"Error during background save: {error_message}")
        if hasattr(self.window, 'notification_banner'):
            self.window.notification_banner.show_message(f"Error saving chat: {error_message}", 10000, "error")
