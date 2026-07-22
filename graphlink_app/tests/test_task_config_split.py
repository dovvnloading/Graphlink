"""R4.1: the graphlink_config -> graphlink_task_config re-export shim.

The split moved mutable module state (OLLAMA_MODELS/CURRENT_MODEL) into
graphlink_task_config while every legacy Qt call site keeps reading through
graphlink_config. These tests pin the two sharp edges of that arrangement:
the dict must be the SAME object through both modules, and CURRENT_MODEL -
a rebound str global that a plain from-import would freeze at import time -
must stay live through graphlink_config's module __getattr__ delegation.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_config
import graphlink_task_config


@pytest.fixture(autouse=True)
def _restore_ollama_globals():
    original_models = dict(graphlink_task_config.OLLAMA_MODELS)
    original_current_model = graphlink_task_config.CURRENT_MODEL
    yield
    graphlink_task_config.OLLAMA_MODELS.clear()
    graphlink_task_config.OLLAMA_MODELS.update(original_models)
    graphlink_task_config.CURRENT_MODEL = original_current_model


def test_ollama_models_is_the_same_object_through_both_modules():
    assert graphlink_config.OLLAMA_MODELS is graphlink_task_config.OLLAMA_MODELS


def test_current_model_reads_stay_live_through_graphlink_config():
    # set_current_model rebinds graphlink_task_config.CURRENT_MODEL; a stale
    # from-import re-export would keep returning the old value here.
    graphlink_config.set_current_model("split-proof-model")
    assert graphlink_task_config.CURRENT_MODEL == "split-proof-model"
    assert graphlink_config.CURRENT_MODEL == "split-proof-model"


def test_current_model_is_not_shadowed_on_the_shim():
    # If anything ever assigns graphlink_config.CURRENT_MODEL directly, the
    # module attribute would permanently shadow the __getattr__ delegation
    # and reads through the shim would silently go stale. Keep it absent.
    assert "CURRENT_MODEL" not in vars(graphlink_config)


def test_task_constants_match_through_both_modules():
    for name in (
        "TASK_TITLE", "TASK_CHAT", "TASK_CHART", "TASK_IMAGE_GEN",
        "TASK_WEB_VALIDATE", "TASK_WEB_SUMMARIZE",
        "API_PROVIDER_OPENAI", "API_PROVIDER_ANTHROPIC", "API_PROVIDER_GEMINI",
        "LOCAL_PROVIDER_OLLAMA", "LOCAL_PROVIDER_LLAMACPP",
        "MODE_OLLAMA_LOCAL", "MODE_LLAMACPP_LOCAL", "MODE_API_ENDPOINT",
    ):
        assert getattr(graphlink_config, name) == getattr(graphlink_task_config, name)
