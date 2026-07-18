"""Tests for the bounded GGUF filesystem scan.

Regression coverage for an unbounded model scan: the "system" scan mode walks the
user's entire Downloads/Documents/Desktop trees (see _iter_existing_llama_cpp_scan_roots)
with a completely unbounded os.walk - a pathological or cloud-synced tree could make the
scan run for a very long time. _collect_gguf_files_from_root now bails out once it has
visited too many directories or spent too long, and reports that via a `truncated` flag
instead of silently returning an incomplete list as if it were complete.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_provider


def _make_gguf_tree(base, structure):
    """structure: list of relative path strings (dirs implied by parents, files by extension)."""
    for rel_path in structure:
        full_path = base / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(b"")


class TestCollectGgufFilesFromRootUnderNormalConditions:
    def test_finds_all_gguf_files_in_a_small_tree(self, tmp_path):
        _make_gguf_tree(tmp_path, [
            "model_a.gguf",
            "subdir/model_b.gguf",
            "subdir/nested/model_c.GGUF",
            "subdir/not_a_model.txt",
        ])

        models, truncated = api_provider._collect_gguf_files_from_root(tmp_path)

        assert len(models) == 3
        assert truncated is False

    def test_skips_blocklisted_directories(self, tmp_path):
        _make_gguf_tree(tmp_path, [
            "model_a.gguf",
            "node_modules/model_b.gguf",
            ".git/model_c.gguf",
            "venv/model_d.gguf",
        ])

        models, truncated = api_provider._collect_gguf_files_from_root(tmp_path)

        assert len(models) == 1
        assert truncated is False


class TestCollectGgufFilesFromRootIsBounded:
    def test_stops_and_reports_truncated_after_too_many_directories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_provider, "_GGUF_SCAN_MAX_DIRECTORIES", 3)
        for i in range(10):
            (tmp_path / f"dir_{i}").mkdir()
            (tmp_path / f"dir_{i}" / "model.gguf").write_bytes(b"")

        models, truncated = api_provider._collect_gguf_files_from_root(tmp_path)

        assert truncated is True
        # Bailing out early means it did not find every model - fewer than the 10 present.
        assert len(models) < 10

    def test_stops_and_reports_truncated_after_time_budget_exceeded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_provider, "_GGUF_SCAN_MAX_SECONDS", -1)  # already "expired"
        (tmp_path / "model.gguf").write_bytes(b"")

        models, truncated = api_provider._collect_gguf_files_from_root(tmp_path)

        assert truncated is True

    def test_small_tree_under_generous_bounds_is_not_truncated(self, tmp_path):
        (tmp_path / "model.gguf").write_bytes(b"")

        models, truncated = api_provider._collect_gguf_files_from_root(tmp_path)

        assert truncated is False
        assert len(models) == 1


class TestScanLocalLlamaCppModelsPropagatesTruncation:
    def test_truncated_flag_present_and_false_for_a_normal_scan(self, tmp_path):
        (tmp_path / "model.gguf").write_bytes(b"")

        result = api_provider.scan_local_llama_cpp_models(str(tmp_path))

        assert result["truncated"] is False
        assert len(result["models"]) == 1

    def test_truncated_flag_true_when_a_root_gets_capped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_provider, "_GGUF_SCAN_MAX_DIRECTORIES", 1)
        for i in range(5):
            (tmp_path / f"dir_{i}").mkdir()

        result = api_provider.scan_local_llama_cpp_models(str(tmp_path))

        assert result["truncated"] is True
