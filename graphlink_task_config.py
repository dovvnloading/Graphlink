"""Qt-free task/provider/model configuration (Qt-removal plan R4.1).

The agent layer's half of what used to live in graphlink_config.py: task
identifiers, provider/mode names, and the runtime model-selection state
(OLLAMA_MODELS/CURRENT_MODEL). Split out so api_provider and the new
backend can import all of it without pulling PySide6 into the process -
graphlink_config.py's module-level Qt imports made every consumer a Qt
process even when it only needed these plain constants.

graphlink_config.py re-exports everything here for the legacy Qt call
sites, so nothing legacy changes behavior; new code (backend/) imports
this module directly and must never import graphlink_config.

This file must stay Qt-free forever - it exists to be importable from
backend/, which test_no_qt_anywhere.py holds to zero tolerance.
"""

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
