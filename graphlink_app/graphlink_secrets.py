"""Secret-at-rest protection for settings values.

API keys and the GitHub token used to be stored as plaintext JSON in
~/.graphlink/session.dat, world-readable by default. This module wraps Windows DPAPI
(CryptProtectData/CryptUnprotectData via ctypes - no new dependencies) so those
values are encrypted at rest, bound to the current Windows user account.

Storage format: "dpapi:" + base64(DPAPI blob), kept in the same JSON string fields as
before, so the settings file shape and the atomic-write machinery are unchanged.

Design tradeoffs, chosen deliberately:
- User-account scoping means a session.dat copied to another machine (or another
  Windows account) can no longer decrypt its secrets - unprotect() returns "" for
  those, which the app treats as "key not configured". Losing silent cross-machine
  portability of *secrets* is the point of encrypting them.
- On non-Windows platforms (or if DPAPI errors), protect() falls back to returning
  the plaintext unchanged - exactly the pre-existing behavior, so nothing regresses;
  the app is Windows-primary (see CONTRIBUTING.md).
- Legacy plaintext values remain readable forever: unprotect() passes through any
  value without the "dpapi:" prefix untouched. SettingsManager migrates them to
  encrypted form on load.
"""

import base64
import ctypes
import sys

_DPAPI_PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _dpapi_call(data: bytes, encrypt: bool) -> bytes | None:
    """Run CryptProtectData/CryptUnprotectData over raw bytes.

    Returns None when DPAPI is unavailable (non-Windows) or the call fails (e.g. a
    blob bound to a different user account).
    """
    if sys.platform != "win32":
        return None

    try:
        import ctypes.wintypes

        class _DataBlob(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()

        crypt32 = ctypes.windll.crypt32
        fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        # (pDataIn, szDataDescr/ppszDataDescr, pOptionalEntropy, pvReserved,
        #  pPromptStruct, dwFlags, pDataOut)
        succeeded = fn(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not succeeded:
            return None

        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def is_protected(value) -> bool:
    """Cheap prefix check - a value *claims* to be protected. NOT sufficient to decide
    whether protect() should skip re-encrypting, because a plaintext secret can legally
    start with the prefix too (e.g. a user types "dpapi:..." as a proxy master key);
    use _is_encrypted_blob for that. Used by unprotect() to decide whether to attempt
    decryption at all."""
    return isinstance(value, str) and value.startswith(_DPAPI_PREFIX)


def _is_encrypted_blob(value: str) -> bool:
    """True only if value is a GENUINELY decryptable DPAPI blob: prefix present, valid
    base64, and CryptUnprotectData succeeds. A plaintext that merely starts with the
    prefix fails the base64/decrypt check, so protect() treats it as plaintext and
    encrypts it (fixing the in-band-prefix collision where such a value would otherwise
    be stored as plaintext and read back as "")."""
    if not is_protected(value):
        return False
    try:
        blob = base64.b64decode(value[len(_DPAPI_PREFIX):], validate=True)
    except Exception:
        return False
    return _dpapi_call(blob, encrypt=False) is not None


def protect(value: str) -> str:
    """Encrypt a secret for storage. Empty stays empty; a value that is already a real
    encrypted blob passes through unchanged (idempotent - so the migration pass doesn't
    double-wrap); on any DPAPI failure the plaintext is returned unchanged (the
    pre-existing at-rest behavior, so non-Windows platforms never regress).

    Note: a plaintext secret that happens to start with "dpapi:" is correctly encrypted
    here (it is not a decryptable blob), rather than mistaken for already-protected."""
    normalized = str(value or "")
    if not normalized or _is_encrypted_blob(normalized):
        return normalized

    encrypted = _dpapi_call(normalized.encode("utf-8"), encrypt=True)
    if encrypted is None:
        return normalized
    return _DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect(value: str) -> str:
    """Decrypt a stored secret. Legacy plaintext (no prefix) passes through unchanged.
    A protected value that cannot be decrypted (different user account, corrupt blob,
    non-Windows platform) returns "" - the app treats that as 'not configured' rather
    than handing garbage bytes to a provider client."""
    normalized = str(value or "")
    if not is_protected(normalized):
        return normalized

    try:
        blob = base64.b64decode(normalized[len(_DPAPI_PREFIX):], validate=True)
    except Exception:
        return ""

    decrypted = _dpapi_call(blob, encrypt=False)
    if decrypted is None:
        return ""
    try:
        return decrypted.decode("utf-8")
    except UnicodeDecodeError:
        return ""
