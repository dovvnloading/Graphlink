"""Regression test for doc/ARCHITECTURE_REVIEW_FINDINGS.md #8: eight files in
graphite_widgets/ carried a UTF-8 BOM, which trips up BOM-unaware tooling (naive
open()/grep/patch flows). Guards against the BOM being reintroduced."""

import codecs
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def test_no_python_source_file_in_the_repo_starts_with_a_utf8_bom():
    offenders = []
    for path in PACKAGE_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.read_bytes()[:3] == codecs.BOM_UTF8:
            offenders.append(str(path.relative_to(PACKAGE_DIR)))

    assert offenders == []
