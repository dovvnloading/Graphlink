"""Real Windows ACL tests for the headless MCP token-file security boundary.

These tests run on windows-latest alongside Graphlink's existing DPAPI tests.
They inspect the actual on-disk DACL after os.replace, not just the call to
os.chmod (which cannot enforce ACL permissions on Windows).
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from backend.headless import _write_token_file
from backend.windows_token_file import _assert_private_dacl

_WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows ACL APIs are required")


def test_restricted_acl_parser_refuses_unprotected_or_broad_grants():
    sid = "S-1-5-21-100-200-300-1001"
    good = f"D:P(A;;FA;;;{sid})(A;;FA;;;SY)(A;;FA;;;BA)"
    _assert_private_dacl(good, sid)
    # The API uses these aliases for built-in local identities; other
    # accounts cannot claim the shorthand to broaden the allowlist.
    built_in_admin = sid.rsplit("-", 1)[0] + "-500"
    _assert_private_dacl(good.replace(sid, built_in_admin).replace(
        built_in_admin, "LA"
    ), built_in_admin)
    with pytest.raises(OSError, match="principal"):
        _assert_private_dacl(good.replace(sid, "LA"), sid)

    for wrong in (
        good.replace("D:P", "D:"),
        good.replace(f"(A;;FA;;;{sid})", "(A;;FA;;;WD)"),
        good + "(A;;FA;;;BU)",
        good.replace("(A;;FA;;;BA)", "(D;;FA;;;BA)"),
        good.replace("(A;;FA;;;BA)", "(A;ID;FA;;;BA)"),
        good.replace("(A;;FA;;;BA)", "(A;;FR;;;BA)"),
    ):
        with pytest.raises(OSError, match="ACL|DACL"):
            _assert_private_dacl(wrong, sid)


def _on_disk_acl(path: Path) -> tuple[str, str]:
    from backend.windows_token_file import (
        _api, _assert_private_dacl, _current_user_sid, _dacl_sddl,
    )

    advapi, kernel = _api()
    sid = _current_user_sid(advapi, kernel)
    # Open the FINAL path, after the atomic replace, and inspect the
    # actual filesystem handle rather than trusting the staging metadata.
    handle = kernel.CreateFileW(
        str(path), 0x80000000, 0x7, None, 3, 0x80, None,
    )
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        observed = _dacl_sddl(handle, advapi, kernel)
        _assert_private_dacl(observed, sid)
        return observed, sid
    finally:
        kernel.CloseHandle(handle)


@_WINDOWS_ONLY
def test_windows_token_file_has_protected_user_system_admin_acl(tmp_path):
    token_file = tmp_path / "custom" / "subdir" / "headless.token"
    _write_token_file(token_file, "first-secret")
    assert token_file.read_text(encoding="utf-8").strip() == "first-secret"
    dacl, user_sid = _on_disk_acl(token_file)

    assert dacl.startswith("D:P"), "custom directory permissions must not be inherited"
    assert user_sid in dacl or (user_sid.endswith("-500") and ";;;LA)" in dacl)
    assert not any(sid in dacl for sid in (";;;WD)", ";;;BU)", ";;;AU)"))
    assert len(list(token_file.parent.glob(f".{token_file.name}.*"))) == 0


@_WINDOWS_ONLY
def test_windows_rotation_replaces_legacy_token_with_new_private_acl(tmp_path):
    token_file = tmp_path / "headless.token"
    token_file.write_text("previous-launch-secret", encoding="utf-8")
    _write_token_file(token_file, "new-readonly-secret")
    assert token_file.read_text(encoding="utf-8").strip() == "new-readonly-secret"
    _on_disk_acl(token_file)


@_WINDOWS_ONLY
def test_windows_acl_verification_failure_never_publishes_token(tmp_path, monkeypatch):
    import backend.windows_token_file as secure

    token_file = tmp_path / "headless.token"
    token_file.write_text("previous-launch-secret", encoding="utf-8")

    def refuse_bad_acl(_sddl, _sid):
        raise OSError("injected Windows ACL verification failure")

    monkeypatch.setattr(secure, "_assert_private_dacl", refuse_bad_acl)
    with pytest.raises(OSError, match="ACL verification"):
        _write_token_file(token_file, "secret-that-must-never-be-published")

    assert token_file.read_text(encoding="utf-8") == "previous-launch-secret"
    assert not list(tmp_path.glob(".headless.token.*"))
