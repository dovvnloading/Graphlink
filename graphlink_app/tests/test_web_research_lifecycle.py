"""Lifecycle, UI-state, and persistence regressions for Web Research."""

import time
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication

from graphlink_plugins.web_research.domain import (
    CancellationToken,
    ResearchCitation,
    ResearchResult,
    ResearchSource,
    RequestCancelled,
)
from graphlink_plugins.web_research.worker import WebResearchWorker
from graphlink_web import WebNode


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


def test_web_node_exposes_cancel_state_without_disabling_recovery():
    node = WebNode(parent_node=None)
    requested = []
    node.cancel_requested.connect(requested.append)
    node.set_running_state(True)

    assert node.run_button.isEnabled()
    assert node.run_button.text() == "Stop Research"
    node.run_button.click()
    _APP.processEvents()
    assert requested == [node]


def test_web_node_dispose_stops_only_its_own_worker():
    node = WebNode(parent_node=None)
    worker = MagicMock()
    worker.isRunning.return_value = True
    node.worker_thread = worker
    node.set_running_state(True)

    node.dispose()

    worker.stop.assert_called_once_with()
    assert node.is_disposed is True
    assert node.worker_thread is None
    assert node.is_running is False


def test_web_result_round_trip_preserves_citations_warnings_and_sources():
    node = WebNode(parent_node=None)
    payload = _result().to_dict()

    node.restore_research_result(payload)

    assert node.research_result.answer_markdown == "A cited answer [s1]."
    assert node.research_result.sources[0].status == "accepted"
    assert node.research_result.citations[0].marker == "[s1]"
    assert node.warnings == ["One source was truncated."]
    assert node.research_result_payload == payload
    assert node.source_count_label.text() == "1 source"


def test_web_error_clears_stale_result_metadata():
    node = WebNode(parent_node=None)
    node.set_result("old answer", _result().sources, research_result=_result())

    node.set_error("network unavailable")

    assert node.research_result is None
    assert node.research_result_payload == {}
    assert node.sources == []
    assert node.warnings == []
    assert node.summary == ""
