from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from graphite_styles import THEMES

CURRENT_THEME = "dark"

def get_current_palette():
    return THEMES[CURRENT_THEME]["palette"]


def is_monochrome_theme():
    return CURRENT_THEME == "mono"


def get_semantic_color(name: str) -> QColor:
    palette = get_current_palette()

    if name == "search_highlight":
        return QColor(palette.NAV_HIGHLIGHT)
    if name == "status_info":
        return QColor(palette.AI_NODE)
    if name == "status_success":
        return QColor(palette.USER_NODE)
    if name == "status_error":
        return QColor("#9a9a9a") if is_monochrome_theme() else QColor("#e74c3c")
    if name == "status_warning":
        return QColor("#a8a8a8") if is_monochrome_theme() else QColor("#f5c04f")
    if name == "artifact":
        return QColor("#8f8f8f") if is_monochrome_theme() else QColor("#00bcd4")
    if name == "conversation_user_bubble":
        return QColor("#595959") if is_monochrome_theme() else QColor("#2b5278")
    if name == "conversation_ai_bubble":
        return QColor("#323232")
    return QColor(palette.SELECTION)


def get_neutral_button_colors():
    if is_monochrome_theme():
        return {
            "background": QColor("#555555"),
            "hover": QColor("#666666"),
            "pressed": QColor("#4a4a4a"),
            "border": QColor("#666666"),
            "icon": QColor("#ffffff"),
            "muted_icon": QColor("#d5d5d5"),
        }

    return {
        "background": QColor("#3a3a3a"),
        "hover": QColor("#474747"),
        "pressed": QColor("#303030"),
        "border": QColor("#4d4d4d"),
        "icon": QColor("#f3f3f3"),
        "muted_icon": QColor("#d8d8d8"),
    }


def get_graph_node_colors():
    button_colors = get_neutral_button_colors()
    return {
        "border": button_colors["border"],
        "header": button_colors["muted_icon"],
        "dot": button_colors["border"],
        "hover_dot": button_colors["hover"],
        "hover_outline": button_colors["hover"].lighter(112),
        "selected_outline": button_colors["hover"].lighter(124),
        "body_start": QColor("#2d2d2d"),
        "body_end": QColor("#252526"),
        "header_start": QColor("#3a3a3a"),
        "header_end": QColor("#303030"),
        "badge_fill": QColor("#4a4a4a"),
        "panel_fill": QColor("#1e1e1e"),
        "panel_border": button_colors["border"],
    }

def apply_theme(app: QApplication, theme_name: str):
    global CURRENT_THEME
    if theme_name in THEMES:
        CURRENT_THEME = theme_name
    else:
        print(f"Warning: Theme '{theme_name}' not found. Defaulting to 'dark'.")
        CURRENT_THEME = "dark"
    
    stylesheet = THEMES[CURRENT_THEME]["stylesheet"]
    app.setStyleSheet(stylesheet)
    
    for widget in app.topLevelWidgets():
        if hasattr(widget, 'on_theme_changed'):
            widget.on_theme_changed()

TASK_TITLE = "task_title"
TASK_CHAT = "task_chat"
TASK_CHART = "task_chart"
TASK_IMAGE_GEN = "task_image_gen"
TASK_WEB_VALIDATE = "task_web_validate"
TASK_WEB_SUMMARIZE = "task_web_summarize"

API_PROVIDER_OPENAI = "OpenAI-Compatible"
API_PROVIDER_GEMINI = "Google Gemini"

OLLAMA_MODELS = {
    TASK_TITLE: 'qwen3:8b',
    TASK_CHAT: 'qwen3:8b',
    TASK_CHART: 'deepseek-coder:6.7b',
    TASK_WEB_VALIDATE: 'qwen3:8b',
    TASK_WEB_SUMMARIZE: 'qwen3:8b'
}

CURRENT_MODEL = OLLAMA_MODELS[TASK_CHAT]

def set_current_model(model_name: str):
    global CURRENT_MODEL
    if model_name:
        CURRENT_MODEL = model_name
        OLLAMA_MODELS[TASK_CHAT] = model_name
