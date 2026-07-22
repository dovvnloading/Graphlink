from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication
from graphlink_styles import FONT_FAMILY_NAME, THEME_TOKENS, THEMES

CURRENT_THEME = "dark"

def get_current_palette():
    return THEMES[CURRENT_THEME]["palette"]


def canvas_font(scene=None, delta=0, weight=QFont.Weight.Normal):
    """Return a canvas font using the scene's live typography settings.

    Canvas items are painted manually, so widget-level application styles do not
    reach their headers. Keeping this small helper in the shared config module
    makes those headers follow the same family and scale as document-backed nodes.
    """
    family = getattr(scene, "font_family", FONT_FAMILY_NAME) if scene else FONT_FAMILY_NAME
    base_size = getattr(scene, "font_size", 10) if scene else 10
    font = QFont(family, max(1, int(base_size) + int(delta)), weight)
    return font


def canvas_font_color(scene=None, fallback=None):
    color = getattr(scene, "font_color", None) if scene else None
    if color is not None:
        return QColor(color)
    if fallback is not None:
        return QColor(fallback)
    # UI-refactor P0: the default falls back to the theme's primary text
    # surface role instead of a hardcoded literal.
    return QColor(get_surface_color("text_primary"))


def get_syntax_color(name: str):
    """Look up a syntax-highlight role's hex string for the current theme
    (keyword/builtin/number/string/comment/function). Sweep adjudication
    moved PythonHighlighter's palette into THEME_TOKENS; this is its lookup."""
    return THEME_TOKENS[CURRENT_THEME]["syntax"][name]


def get_surface_color(name: str):
    """Look up a neutral surface/text role's hex string for the current theme.

    UI-refactor P0 (doc/UI_QA_AUDIT.md section 7): the lookup the hex-literal
    sweep migrated node/canvas/widget chrome onto. Mirrors
    get_semantic_color()'s table-lookup shape but returns the raw hex string
    rather than a QColor - the dominant call sites are f-string stylesheets,
    and QColor construction is one wrap away for the painting sites that
    need it."""
    tokens = THEME_TOKENS[CURRENT_THEME]["surface"]
    return tokens[name]


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

# Qt-removal plan R4.1: the task/provider/model half of this module moved to
# the Qt-free graphlink_task_config so api_provider and the new backend can
# import it without pulling PySide6 into the process. Legacy call sites keep
# reading everything through this module unchanged:
# - the constants are immutable strings (safe to re-export by from-import),
# - OLLAMA_MODELS is the SAME dict object (mutations flow both ways),
# - set_current_model/sync_ollama_task_models are the same function objects
#   and mutate graphlink_task_config's own globals,
# - CURRENT_MODEL is a rebound str global, so a from-import would go stale
#   the moment set_current_model reassigns it; the module __getattr__ below
#   delegates reads live instead. (PEP 562 __getattr__ only fires for names
#   missing from this module's dict - do not from-import CURRENT_MODEL here.)
from graphlink_task_config import (
    TASK_TITLE,
    TASK_CHAT,
    TASK_CHART,
    TASK_IMAGE_GEN,
    TASK_WEB_VALIDATE,
    TASK_WEB_SUMMARIZE,
    API_PROVIDER_OPENAI,
    API_PROVIDER_ANTHROPIC,
    API_PROVIDER_GEMINI,
    LOCAL_PROVIDER_OLLAMA,
    LOCAL_PROVIDER_LLAMACPP,
    MODE_OLLAMA_LOCAL,
    MODE_LLAMACPP_LOCAL,
    MODE_API_ENDPOINT,
    OLLAMA_MODELS,
    set_current_model,
    sync_ollama_task_models,
)


def __getattr__(name):
    if name == "CURRENT_MODEL":
        import graphlink_task_config
        return graphlink_task_config.CURRENT_MODEL
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
