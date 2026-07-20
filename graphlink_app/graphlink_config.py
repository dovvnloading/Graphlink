from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication
from graphlink_styles import THEME_TOKENS, THEMES

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
    """Look up a semantic role's color for the current theme.

    A table lookup against graphlink_styles.THEME_TOKENS, not a per-theme
    formula: every role's resolved value (whether it used to be derived from
    a palette color, computed via QColor.darker()/.lighter(), or a plain
    per-theme literal) is captured once in the token table.

    Known drift risk, documented rather than restructured: four of these
    roles (search_highlight/status_info/status_success/the unrecognized-name
    fallback) are pure aliases of a palette color in every theme, and two more
    (artifact, conversation_user_bubble) alias or derive from a palette color
    in some themes but not others (mono gets its own independent literal for
    both). Unlike get_graph_node_colors()'s button-derived keys, this aliasing
    is theme-conditional rather than uniform, so it is not re-expressed as a
    live lookup here - edit both THEME_TOKENS["semantic"] and the relevant
    palette entry together if either changes.
    """
    tokens = THEME_TOKENS[CURRENT_THEME]["semantic"]
    return QColor(tokens.get(name, tokens["default"]))


def get_neutral_button_colors():
    """Look up the current theme's neutral button color set from THEME_TOKENS."""
    tokens = THEME_TOKENS[CURRENT_THEME]["neutral_button"]
    return {key: QColor(value) for key, value in tokens.items()}


def get_graph_node_colors():
    """Return the current theme's graph node color set.

    Only body_start/body_end/header_start/header_end/badge_fill/panel_fill are
    independent per-theme literals, looked up from THEME_TOKENS. The other
    seven keys are not independent tokens - in every theme, border/dot/
    panel_border alias neutral_button's border, header aliases its muted_icon,
    hover_dot aliases its hover, and hover_outline/selected_outline are
    QColor.lighter(112)/.lighter(124) of that same hover color. Deriving them
    live from get_neutral_button_colors() here (matching the original
    per-theme branching logic exactly) keeps that relationship real instead of
    flattening it into seven more places a theme edit would need to touch by
    hand without anything enforcing it.
    """
    button_colors = get_neutral_button_colors()
    tokens = THEME_TOKENS[CURRENT_THEME]["graph_node"]
    return {
        "border": button_colors["border"],
        "header": button_colors["muted_icon"],
        "dot": button_colors["border"],
        "hover_dot": button_colors["hover"],
        "hover_outline": button_colors["hover"].lighter(112),
        "selected_outline": button_colors["hover"].lighter(124),
        "body_start": QColor(tokens["body_start"]),
        "body_end": QColor(tokens["body_end"]),
        "header_start": QColor(tokens["header_start"]),
        "header_end": QColor(tokens["header_end"]),
        "badge_fill": QColor(tokens["badge_fill"]),
        "panel_fill": QColor(tokens["panel_fill"]),
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

    # topLevelWidgets() above only reaches widgets that are themselves
    # top-level windows - WebIslandHost is a plain child QFrame, so island
    # hosts parented inside a window (notification, command-palette, and any
    # future settings island) were never actually reached by that loop.
    # theme_changed_all() republishes to every registered host directly.
    import graphlink_web_island_host
    graphlink_web_island_host.theme_changed_all()

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
