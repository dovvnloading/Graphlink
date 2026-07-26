"""Qt worker adapter for the Qt-free Web Research service."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .domain import CancellationToken, ProgressEvent, RequestCancelled, WebResearchRequest
from .service import WebResearchService


class WebResearchWorker(QThread):
    progress = Signal(object)
    finished = Signal(object)
    error = Signal(object)
    cancelled = Signal(object)

    def __init__(self, request: WebResearchRequest, *, service: WebResearchService | None = None, parent=None):
        super().__init__(parent)
        self.request = request
        self.service = service
        self.token = CancellationToken()

    def run(self):
        try:
            service = self.service or WebResearchService()
            result = service.run(self.request, token=self.token, progress=self.progress.emit)
            if self.token.cancelled:
                self.cancelled.emit(self.request.request_id)
                return
            self.finished.emit(result)
        except RequestCancelled:
            self.cancelled.emit(self.request.request_id)
        except Exception as exc:
            if self.token.cancelled:
                self.cancelled.emit(self.request.request_id)
            else:
                self.error.emit(exc)

    def stop(self):
        self.token.cancel()
        self.requestInterruption()
