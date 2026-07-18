"""Tests for graphlink_logging.configure_logging().

Regression coverage for the app's missing logging setup: nothing in the app ever
configured Python's logging module, so the one existing logging.exception() call
(content_codec.py, corrupted image data during deserialization) went to stderr's
"handler of last resort" - invisible in a windowed app with no console. configure_logging()
now attaches a rotating file handler to the root logger so anything that calls logging.*
lands somewhere durable.

The root logger is process-global and shared with every other test in the suite, so each
test here removes the handler it added and resets the module's _configured guard in
teardown - otherwise a leftover handler pointing at a deleted tmp_path directory could
raise on unrelated log calls elsewhere in the suite.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_logging


def _rotating_file_handlers():
    return [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


class TestConfigureLogging:
    def teardown_method(self):
        root_logger = logging.getLogger()
        for handler in _rotating_file_handlers():
            root_logger.removeHandler(handler)
            handler.close()
        graphlink_logging._configured = False

    def test_creates_the_log_files_parent_directory(self, tmp_path):
        log_path = tmp_path / "nested" / "graphlink.log"

        graphlink_logging.configure_logging(log_path)

        assert log_path.parent.is_dir()

    def test_attaches_exactly_one_rotating_file_handler_pointed_at_the_given_path(self, tmp_path):
        log_path = tmp_path / "graphlink.log"

        graphlink_logging.configure_logging(log_path)

        handlers = _rotating_file_handlers()
        assert len(handlers) == 1
        assert Path(handlers[0].baseFilename) == log_path

    def test_calling_it_twice_does_not_duplicate_the_handler(self, tmp_path):
        log_path = tmp_path / "graphlink.log"

        graphlink_logging.configure_logging(log_path)
        graphlink_logging.configure_logging(log_path)

        assert len(_rotating_file_handlers()) == 1

    def test_a_logged_exception_actually_lands_in_the_file(self, tmp_path):
        log_path = tmp_path / "graphlink.log"
        graphlink_logging.configure_logging(log_path)

        try:
            raise ValueError("boom")
        except ValueError:
            logging.exception("Something went wrong")

        contents = log_path.read_text(encoding="utf-8")
        assert "Something went wrong" in contents
        assert "ValueError: boom" in contents

    def test_default_path_is_under_the_graphlink_home_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        graphlink_logging.configure_logging()

        expected_path = tmp_path / ".graphlink" / "graphlink.log"
        assert Path(_rotating_file_handlers()[0].baseFilename) == expected_path
