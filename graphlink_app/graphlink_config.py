from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication
from graphlink_styles import THEMES

CURRENT_THEME = "dark"

def get_current_palette():
    return THEMES[CURRENT_THEME]["palette"]


def canvas_font(scene=None, delta=0, weight=QFont.Weight.Normal):
    """Return a canvas font using the scene's live typography settings.

    Canvas items are painted manually, so widget-level application styles do not
    reach their headers. Keeping this small helper in the shared config module
    makes those headers follow the same family and scale as document-backed nodes.
    """
    family = getattr(scene, "font_family", "Segoe UI") if scene else "Segoe UI"
    base_size = getattr(scene, "font_size", 10) if scene else 10
    font = QFont(family, max(1, int(base_size) + int(delta)), weight)
    return font


def canvas_font_color(scene=None, fallback="#dddddd"):
    color = getattr(scene, "font_color", None) if scene else None
    return QColor(color) if color is not None else QColor(fallback)


def is_monochrome_theme():
    return CURRENT_THEME == "mono"


def is_muted_theme():
    return CURRENT_THEME == "muted"


def get_semantic_color(name: str) -> QColor:
    palette = get_current_palette()

    if name == "search_highlight":
        return QColor(palette.NAV_HIGHLIGHT)
    if name == "status_info":
        return QColor(palette.AI_NODE)
    if name == "status_success":
        return QColor(palette.USER_NODE)
    if name == "status_error":
        return QColor("#9a9a9a") if is_monochrome_theme() else QColor("#9b8588") if is_muted_theme() else QColor("#9f7d80")
    if name == "status_warning":
        return QColor("#b0b0b0") if is_monochrome_theme() else QColor("#9f8a72") if is_muted_theme() else QColor("#a48f6f")
    if name == "artifact":
        return QColor("#8f8f8f") if is_monochrome_theme() else QColor(palette.AI_NODE)
    if name == "conversation_user_bubble":
        return QColor("#595959") if is_monochrome_theme() else QColor(palette.USER_NODE).darker(125)
    if name == "conversation_ai_bubble":
        return QColor("#323232") if is_monochrome_theme() else QColor("#2d333b")
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

    if is_muted_theme():
        return {
            "background": QColor("#333b45"),
            "hover": QColor("#3f4955"),
            "pressed": QColor("#2f3741"),
            "border": QColor("#506071"),
            "icon": QColor("#d4dce5"),
            "muted_icon": QColor("#b0bcc9"),
        }

    return {
        "background": QColor("#323a44"),
        "hover": QColor("#3f4a55"),
        "pressed": QColor("#2d353e"),
        "border": QColor("#4f5965"),
        "icon": QColor("#edf1f5"),
        "muted_icon": QColor("#b4bec9"),
    }


def get_graph_node_colors():
    button_colors = get_neutral_button_colors()
    if is_muted_theme():
        return {
            "border": button_colors["border"],
            "header": button_colors["muted_icon"],
            "dot": button_colors["border"],
            "hover_dot": button_colors["hover"],
            "hover_outline": button_colors["hover"].lighter(112),
            "selected_outline": button_colors["hover"].lighter(124),
            "body_start": QColor("#2b3138"),
            "body_end": QColor("#24292f"),
            "header_start": QColor("#353e48"),
            "header_end": QColor("#2d343c"),
            "badge_fill": QColor("#414b56"),
            "panel_fill": QColor("#191c20"),
            "panel_border": button_colors["border"],
        }

    return {
        "border": button_colors["border"],
        "header": button_colors["muted_icon"],
        "dot": button_colors["border"],
        "hover_dot": button_colors["hover"],
        "hover_outline": button_colors["hover"].lighter(112),
        "selected_outline": button_colors["hover"].lighter(124),
        "body_start": QColor("#2b3139"),
        "body_end": QColor("#252a31"),
        "header_start": QColor("#353d48"),
        "header_end": QColor("#2d343d"),
        "badge_fill": QColor("#3f4954"),
        "panel_fill": QColor("#1d2024"),
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
API_PROVIDER_ANTHROPIC = "Anthropic Claude"
API_PROVIDER_GEMINI = "Google Gemini"

LOCAL_PROVIDER_OLLAMA = "Ollama"
LOCAL_PROVIDER_LLAMACPP = "Llama.cpp"

MODE_OLLAMA_LOCAL = "Ollama (Local)"
MODE_LLAMACPP_LOCAL = "Llama.cpp (Local)"
MODE_API_ENDPOINT = "API Endpoint"

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


def sync_ollama_task_models(settings_manager):
    """Populate the per-task Ollama model table from the user's own persisted settings.

    api_provider.chat() resolves the Ollama model for a task with a plain
    OLLAMA_MODELS.get(task) lookup - it has no per-call fallback logic. TASK_CHART,
    TASK_WEB_VALIDATE, and TASK_WEB_SUMMARIZE each have their own independent settings
    field now (see OllamaSettingsWidget), each with its own sensible get_ollama_*_model
    fallback (chart falls back to a code-specialized default, web validate/summarize
    fall back to the chat model) - so this has to read through the settings manager
    rather than just copying whatever the chat model happens to be, the way
    set_current_model() does for TASK_CHAT alone.

    Call this once at startup (after set_current_model) and again whenever
    OllamaSettingsWidget.save_settings() persists a change, so the new selection takes
    effect in the current session without a restart.
    """
    OLLAMA_MODELS[TASK_CHART] = settings_manager.get_ollama_chart_model()
    OLLAMA_MODELS[TASK_WEB_VALIDATE] = settings_manager.get_ollama_web_validate_model()
    OLLAMA_MODELS[TASK_WEB_SUMMARIZE] = settings_manager.get_ollama_web_summarize_model()
