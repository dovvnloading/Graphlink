"""Lifecycle regressions for the Web Research worker thread.

WebNode (the legacy Qt canvas node) was deleted once the Web-Research
plugin was fully ported to the Qt-free backend/frontend stack; its
UI-state/persistence tests were removed with it. WebResearchWorker itself
is untouched and still covered here.
"""

import time

from PySide6.QtWidgets import QApplication

from graphlink_plugins.web_research.domain import (
    CancellationToken,
    ResearchCitation,
    ResearchResult,
    ResearchSource,
    RequestCancelled,
)
from graphlink_plugins.web_research.worker import WebResearchWorker


_APP = QApplication.instance() or QApplication([])


class _ResultService:
    def __init__(self, result):
        self.result = result

    def run(self, request, *, token, progress):
        progress(type("Progress", (), {"message": "running"})())
        return self.result


class _BlockingService:
    def run(self, request, *, token, progress):
        while True:
            token.raise_if_cancelled()
            time.sleep(0.001)


def _result():
    source = ResearchSource(
        source_id="s1",
        title="A source",
        url="https://example.com/article",
        canonical_url="https://example.com/article",
        final_url="https://example.com/article",
        status="accepted",
    )
    return ResearchResult(
        request_id="request-1",
        original_query="What happened?",
        effective_query="What happened?",
        answer_markdown="A cited answer [s1].",
        sources=[source],
        citations=[ResearchCitation("s1", "[s1]")],
        warnings=["One source was truncated."],
        provider_snapshot={"task": "web_research"},
    )


def _request():
    from graphlink_plugins.web_research.domain import WebResearchRequest

    return WebResearchRequest("request-1", "node-1", 1, "What happened?")


def test_worker_emits_typed_result_and_progress():
    worker = WebResearchWorker(_request(), service=_ResultService(_result()))
    results = []
    progress = []
    worker.finished.connect(results.append)
    worker.progress.connect(progress.append)
    worker.start()
    assert worker.wait(2000)
    _APP.processEvents()

    assert len(results) == 1
    assert isinstance(results[0], ResearchResult)
    assert progress[0].message == "running"


def test_worker_stop_is_cooperative_and_emits_cancelled():
    worker = WebResearchWorker(_request(), service=_BlockingService())
    cancelled = []
    worker.cancelled.connect(cancelled.append)
    worker.start()
    time.sleep(0.02)
    worker.stop()
    assert worker.wait(2000)
    _APP.processEvents()

    assert cancelled == ["request-1"]
