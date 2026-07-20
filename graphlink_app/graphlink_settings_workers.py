"""Background QThread workers for settings model discovery/scanning.

Extracted from graphlink_ui_dialogs/graphlink_settings_dialogs.py (Phase 3,
increment 5) so both the legacy Qt widget (until its Phase 3 increment 10
deletion) and the new SettingsBridge can construct the same workers without
the bridge depending on the widget file it will eventually outlive. None of
these three classes ever touched a Qt widget - each wraps exactly one
api_provider.* blocking call and re-emits the result as a finished/error
signal, so the extraction is a pure move, not a behavior change.

ModelPullWorkerThread (the fourth worker the Phase 3 checklist names) is
NOT here - it already lives in graphlink_agents_tools.py, independent of
this file, and needs no extraction.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

import api_provider
import graphlink_config as config


class OllamaModelScanWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, scan_path=None, parent=None):
        super().__init__(parent)
        self.scan_path = scan_path

    def run(self):
        try:
            results = api_provider.scan_local_ollama_models(self.scan_path)
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


class ApiModelLoadWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, provider, api_key, base_url=None, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url

    def run(self):
        try:
            # Discovery is deliberately isolated from the GUI thread.  It also
            # exercises the same provider initialization path used by Save, so a
            # successful catalog load is a useful connection check.
            api_provider.initialize_api(
                self.provider,
                self.api_key,
                self.base_url if self.provider == config.API_PROVIDER_OPENAI else None,
            )
            descriptors = api_provider.get_available_model_descriptors()
            self.finished.emit([
                {
                    "model_id": descriptor.model_id,
                    "provider": descriptor.provider,
                    "capabilities": sorted(descriptor.capabilities),
                    "ready": descriptor.ready,
                    "available": descriptor.available,
                }
                for descriptor in descriptors
            ])
        except Exception as exc:
            self.error.emit(str(exc))


class LlamaCppModelScanWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, scan_path=None, parent=None):
        super().__init__(parent)
        self.scan_path = scan_path

    def run(self):
        try:
            results = api_provider.scan_local_llama_cpp_models(self.scan_path)
            self.finished.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))
