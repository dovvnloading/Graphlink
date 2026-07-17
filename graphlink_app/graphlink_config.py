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


def canvas_font_color(scene=None, fallback="#DDDDDD"):
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
        return QColor("#9A9A9A") if is_monochrome_theme() else QColor("#8A8A8A") if is_muted_theme() else QColor("#848484")
    if name == "status_warning":
        return QColor("#B0B0B0") if is_monochrome_theme() else QColor("#8D8D8D") if is_muted_theme() else QColor("#919191")
    if name == "artifact":
        return QColor("#8F8F8F") if is_monochrome_theme() else QColor(palette.AI_NODE)
    if name == "conversation_user_bubble":
        return QColor("#595959") if is_monochrome_theme() else QColor(palette.USER_NODE).darker(125)
    if name == "conversation_ai_bubble":
        return QColor("#323232") if is_monochrome_theme() else QColor("#323232")
    return QColor(palette.SELECTION)


def get_neutral_button_colors():
    if is_monochrome_theme():
        return {
            "background": QColor("#555555"),
            "hover": QColor("#666666"),
            "pressed": QColor("#4A4A4A"),
            "border": QColor("#666666"),
            "icon": QColor("#FFFFFF"),
            "muted_icon": QColor("#D5D5D5"),
        }

    if is_muted_theme():
        return {
            "background": QColor("#3A3A3A"),
            "hover": QColor("#484848"),
            "pressed": QColor("#363636"),
            "border": QColor("#5E5E5E"),
            "icon": QColor("#DBDBDB"),
            "muted_icon": QColor("#BABABA"),
        }

    return {
        "background": QColor("#393939"),
        "hover": QColor("#484848"),
        "pressed": QColor("#343434"),
        "border": QColor("#585858"),
        "icon": QColor("#F0F0F0"),
        "muted_icon": QColor("#BDBDBD"),
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
            "body_start": QColor("#303030"),
            "body_end": QColor("#282828"),
            "header_start": QColor("#3D3D3D"),
            "header_end": QColor("#333333"),
            "badge_fill": QColor("#4A4A4A"),
            "panel_fill": QColor("#1C1C1C"),
            "panel_border": button_colors["border"],
        }

    return {
        "border": button_colors["border"],
        "header": button_colors["muted_icon"],
        "dot": button_colors["border"],
        "hover_dot": button_colors["hover"],
        "hover_outline": button_colors["hover"].lighter(112),
        "selected_outline": button_colors["hover"].lighter(124),
        "body_start": QColor("#303030"),
        "body_end": QColor("#292929"),
        "header_start": QColor("#3C3C3C"),
        "header_end": QColor("#333333"),
        "badge_fill": QColor("#484848"),
        "panel_fill": QColor("#202020"),
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

    # Qt creates standard editor context menus internally, so they do not
    # pass through Graphlink's explicit menu factory. Install the process-wide
    # surface guard after the application theme is applied.
    from graphlink_context_menu import install_context_menu_filter
    install_context_menu_filter(app)
    
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
    # These are runtime-resolved selections, not product defaults.  An empty
    # value means the user has not selected a ready local model yet.
    TASK_TITLE: '',
    TASK_CHAT: '',
    TASK_CHART: '',
    TASK_WEB_VALIDATE: '',
    TASK_WEB_SUMMARIZE: ''
}

CURRENT_MODEL = ''

def set_current_model(model_name: str):
    global CURRENT_MODEL
    if model_name:
        CURRENT_MODEL = model_name
        OLLAMA_MODELS[TASK_CHAT] = model_name


def sync_ollama_task_models(settings_manager):
    """Populate runtime selections from explicit/inherited/Auto settings.

    The compatibility table remains for callers that already pass a task to
    :func:`api_provider.chat`, but it no longer contains product-authored model
    IDs.  Cached discovery is intentionally best-effort; an empty result leaves
    an Auto task unconfigured until the provider is reachable and the user picks
    or discovers a model.
    """
    from graphlink_model_catalog import ModelDescriptor, resolve_task_model

    if hasattr(settings_manager, "get_ollama_model_assignments"):
        assignments = settings_manager.get_ollama_model_assignments()
    else:
        assignments = {
            TASK_CHAT: settings_manager.get_ollama_chat_model(),
            TASK_TITLE: settings_manager.get_ollama_title_model(),
            TASK_CHART: settings_manager.get_ollama_chart_model(),
            TASK_WEB_VALIDATE: settings_manager.get_ollama_web_validate_model(),
            TASK_WEB_SUMMARIZE: settings_manager.get_ollama_web_summarize_model(),
        }

    cached_models = []
    if hasattr(settings_manager, "get_ollama_scanned_models"):
        cached_models = settings_manager.get_ollama_scanned_models()
    catalog = [ModelDescriptor(model_id=model, provider=LOCAL_PROVIDER_OLLAMA) for model in cached_models]
    chat_model = resolve_task_model(TASK_CHAT, assignments, catalog)
    for task in (TASK_CHAT, TASK_TITLE, TASK_CHART, TASK_WEB_VALIDATE, TASK_WEB_SUMMARIZE):
        OLLAMA_MODELS[task] = resolve_task_model(
            task,
            assignments,
            catalog,
            chat_model=chat_model,
        )

    global CURRENT_MODEL
    CURRENT_MODEL = OLLAMA_MODELS[TASK_CHAT]
