"""Legacy source cleanup must be opt-in, narrowly matched, and backed up."""

from __future__ import annotations

import os
import sqlite3

import pytest

from backend import knowledge_store as ks
from backend.knowledge_chunking import chunk_text
from backend.knowledge_legacy_cleanup import (
    delete_selected_legacy_branches,
    main,
    preview_legacy_branch_documents,
)


def _insert(db_path, source_uri, title, text, *, collection_id=0, mime="text/plain"):
    return ks.add_document_with_chunks(
        db_path, source_uri=source_uri, title=title, mime=mime,
        text=text, chunks=chunk_text(text), collection_id=collection_id,
    ).document_id


def test_preview_only_identifies_exact_old_scheme_and_does_not_change_data(tmp_path):
    db = tmp_path / "knowledge.db"
    legacy = _insert(db, "branch:n1", "Branch (node n1)", "legacy secret", collection_id=7)
    new = _insert(db, "branch:a01bc4defc01234567891234abcdef01", "Branch (node n2)", "modern text")
    similar = _insert(db, "branch:n2", "An independently ingested document", "manual text")
    preview = preview_legacy_branch_documents(db)
    assert [row["id"] for row in preview] == [legacy]
    assert preview[0]["collection_id"] == 7
    assert ks.get_document(db, new) is not None
    assert ks.get_document(db, similar) is not None
    assert len(ks.list_documents(db)) == 3
    assert list(tmp_path.glob("*.legacy-branch-backup-*.db")) == []


def test_preview_never_calls_the_migrating_write_connection(tmp_path, monkeypatch):
    import backend.knowledge_legacy_cleanup as cleanup

    db = tmp_path / "knowledge.db"
    legacy = _insert(db, "branch:n3", "Branch (node n3)", "old content")

    def disallowed_connect(*_args, **_kwargs):
        pytest.fail("preview must use SQLite URI mode=ro, not the migrating writer")

    monkeypatch.setattr(cleanup, "_connect", disallowed_connect)
    assert [row["id"] for row in preview_legacy_branch_documents(db)] == [legacy]


def test_explicit_ids_and_confirmation_required_before_any_delete(tmp_path):
    db = tmp_path / "knowledge.db"
    legacy = _insert(db, "branch:n1", "Branch (node n1)", "obsolete text")
    modern = _insert(db, "branch:ab012345678901234567890123456789", "Branch (node n1)", "new text")

    with pytest.raises(ValueError, match="confirm-delete"):
        delete_selected_legacy_branches(db, [legacy])
    with pytest.raises(ValueError, match="verified legacy"):
        delete_selected_legacy_branches(db, [modern], confirm=True)
    with pytest.raises(ValueError, match="verified legacy"):
        delete_selected_legacy_branches(db, [legacy, modern], confirm=True)
    with pytest.raises(ValueError, match="positive"):
        delete_selected_legacy_branches(db, [0], confirm=True)
    assert len(ks.list_documents(db)) == 2
    assert not list(tmp_path.glob("*.legacy-branch-backup-*.db"))


def test_selected_cleanup_backups_database_then_removes_only_legacy_rows(tmp_path):
    db = tmp_path / "knowledge.db"
    obsolete = _insert(db, "branch:n7", "Branch (node n7)", "oldambiguousphrase")
    modern = _insert(db, "branch:aa012345678901234567890123456789", "Branch (node n7)", "newuniquephrase")
    manual = _insert(db, "manual.txt", "My notes", "unrelatedphrase")
    result = delete_selected_legacy_branches(db, [obsolete], confirm=True)

    from pathlib import Path
    backup = Path(result["backup_path"])
    assert backup.is_file()
    if os.name != "nt":
        assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (obsolete,)).fetchone()[0] == 1
    assert ks.get_document(db, obsolete) is None
    assert ks.get_document(db, modern) is not None
    assert ks.get_document(db, manual) is not None
    assert not ks.search_chunks(db, "oldambiguousphrase")
    assert len(ks.search_chunks(db, "newuniquephrase")) == 1
    assert preview_legacy_branch_documents(db) == []


def test_cli_defaults_to_preview_and_rejects_unconfirmed_delete(tmp_path, capsys):
    db = tmp_path / "knowledge.db"
    legacy = _insert(db, "branch:n1", "Branch (node n1)", "old entry")
    assert main(["--db", str(db)]) == 0
    assert str(legacy) in capsys.readouterr().out
    with pytest.raises(SystemExit) as err:
        main(["--db", str(db), "--delete-id", str(legacy)])
    assert err.value.code == 2
    assert ks.get_document(db, legacy) is not None
