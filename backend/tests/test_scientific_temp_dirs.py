"""Regression tests for scientific staging and CSV temporary directories."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _run_in_fresh_process(tmp_path: Path, body: str) -> None:
    """Run before any scientific import, with the entire temp namespace isolated."""
    source = (
        "import sys\n"
        "import tempfile\n"
        "from pathlib import Path\n"
        "from unittest.mock import patch\n"
        "with patch.object(tempfile, 'tempdir', sys.argv[1]):\n"
        + textwrap.indent(textwrap.dedent(body).strip() + "\n", "    ")
    )
    directory = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", source, directory],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "TMPDIR": directory, "TEMP": directory, "TMP": directory},
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_import_does_not_create_scientific_temp_directories(tmp_path):
    _run_in_fresh_process(tmp_path, r"""
        import graphlink_plugins.scientific.data_table

        assert not list(Path(sys.argv[1]).glob("graphlink-scientific-*"))
    """)


def test_first_real_use_creates_one_staging_directory_and_csv_is_lazy(tmp_path):
    _run_in_fresh_process(tmp_path, r"""
        from graphlink_plugins.scientific import data_table

        root = Path(sys.argv[1])
        assert not list(root.glob("graphlink-scientific-*"))
        data_table.parse_table(b"Value\n1\n", preview_only=True)
        assert not list(root.glob("graphlink-scientific-*"))
        meta = data_table.parse_table(b"Value\n1\n")
        staging = [p for p in root.glob("graphlink-scientific-*")
                   if not p.name.startswith("graphlink-scientific-csv-")]
        assert len(staging) == 1
        assert Path(data_table.table_store_root()) == staging[0]
        assert not list(root.glob("graphlink-scientific-csv-*"))
        assert data_table.read_table(meta)["rows"] == [[1.0]]

        data_table.table_csv_chunk(meta)
        assert len(list(root.glob("graphlink-scientific-csv-*"))) == 1
    """)
    # Explicit atexit cleanup must leave neither directory behind.
    assert not list(tmp_path.glob("graphlink-scientific-*"))


def test_stale_sweep_only_removes_old_scientific_siblings(tmp_path):
    _run_in_fresh_process(tmp_path, r"""
        import os
        import time

        root = Path(sys.argv[1])
        old = ("graphlink-scientific-old", "graphlink-scientific-csv-old")
        fresh = ("graphlink-scientific-fresh", "graphlink-scientific-csv-fresh")
        for name in (*old, *fresh, "unrelated-old"):
            (root / name).mkdir()
        (root / "graphlink-scientific-old-file").write_text("do not delete")
        two_days_ago = time.time() - 2 * 24 * 60 * 60
        for name in (*old, "unrelated-old"):
            os.utime(root / name, (two_days_ago, two_days_ago))
        os.utime(root / "graphlink-scientific-old-file", (two_days_ago, two_days_ago))

        from graphlink_plugins.scientific import data_table
        assert all((root / name).exists() for name in old)
        data_table.table_store_root()
        assert all(not (root / name).exists() for name in old)
        assert all((root / name).is_dir() for name in fresh)
        assert (root / "unrelated-old").is_dir()
        assert (root / "graphlink-scientific-old-file").is_file()
    """)



def test_stale_sweep_preserves_own_directories_even_if_aged(tmp_path):
    _run_in_fresh_process(tmp_path, r"""
        import os
        import time

        from graphlink_plugins.scientific import data_table

        root = Path(sys.argv[1])
        meta = data_table.parse_table(b"Value\n1\n")
        data_table.table_csv_chunk(meta)
        staging = Path(data_table.table_store_root())
        csv_dir = next(root.glob("graphlink-scientific-csv-*"))
        abandoned = root / "graphlink-scientific-abandoned"
        abandoned.mkdir()
        old = time.time() - 48 * 60 * 60
        for path in (staging, csv_dir, abandoned):
            os.utime(path, (old, old))

        data_table._sweep_stale_directories()
        assert staging.is_dir()
        assert csv_dir.is_dir()
        assert not abandoned.exists()
        assert data_table.read_table(meta)["rows"] == [[1.0]]
    """)
    assert not list(tmp_path.glob("graphlink-scientific-*"))


def test_stale_sweep_swallows_clock_and_cleanup_errors(tmp_path):
    _run_in_fresh_process(tmp_path, r"""
        import os
        import time

        from graphlink_plugins.scientific import data_table

        root = Path(sys.argv[1])
        locked = root / "graphlink-scientific-locked"
        locked.mkdir()
        old = time.time() - 48 * 60 * 60
        os.utime(locked, (old, old))
        with patch.object(data_table.time, "time", side_effect=OSError("clock unavailable")):
            data_table._sweep_stale_directories()
        assert locked.is_dir()
        with patch.object(data_table.shutil, "rmtree", side_effect=PermissionError("locked")):
            data_table._sweep_stale_directories()
        assert locked.is_dir()
        locked.rmdir()
    """)
    assert not list(tmp_path.glob("graphlink-scientific-*"))


def test_worker_store_access_never_creates_private_temp_directories(tmp_path):
    _run_in_fresh_process(tmp_path, r"""
        from graphlink_plugins.scientific import data_table

        root = Path(sys.argv[1])
        with data_table.use_table_store(str(root / "parent-store")):
            receipt = data_table.stage_result_table(
                {"columns": ["Measurement"], "rows": [[1.0]]},
                pack="test", operation_id="worker",
            )
            assert data_table.read_table(receipt)["rows"] == [[1.0]]
        assert not list(root.glob("graphlink-scientific-*"))
    """)


def test_concurrent_first_use_creates_one_staging_directory(tmp_path):
    _run_in_fresh_process(tmp_path, r"""
        from concurrent.futures import ThreadPoolExecutor
        from graphlink_plugins.scientific.data_table import table_store_root

        with ThreadPoolExecutor(max_workers=12) as workers:
            roots = list(workers.map(lambda _: table_store_root(), range(48)))
        assert len(set(roots)) == 1
        assert len(list(Path(sys.argv[1]).glob("graphlink-scientific-*"))) == 1
    """)
