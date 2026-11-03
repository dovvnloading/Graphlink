"""Secure creation of the headless MCP token's temporary file on Windows.

Python's os.chmod(..., 0o600) does NOT set a Windows DACL. Creating an
ordinary tempfile and writing a bearer token before tightening its ACL can
expose the secret to other local accounts if the chosen directory has
permissive inherited access.

On Windows, create the temporary file through CreateFileW with a *protected*
DACL from birth. Only the CURRENT USER, LocalSystem and Administrators are
granted full access. Verify the actual filesystem DACL before returning a
file descriptor to the caller; fail closed on FAT/exFAT or any ACL failure.

The descriptor is created with standard Windows APIs through ctypes. No
pywin32 dependency or localized icacls/username parsing is required.
"""

from __future__ import annotations

import ctypes
import os
import re
import secrets
from ctypes import wintypes
from pathlib import Path

# Win32 constants, not Python's os.chmod POSIX permission bits.
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SDDL_REVISION = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_SE_FILE_OBJECT = 1
_GENERIC_READ_WRITE = 0xC0000000
_CREATE_NEW = 1
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_ALL_ACCESS = 0x001F01FF
_MAX_COLLISION_RETRIES = 12


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


def _api():
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes), wintypes.DWORD,
        wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE

    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    return advapi, kernel


def _failure(action: str, code: int | None = None) -> OSError:
    error = ctypes.get_last_error() if code is None else code
    return OSError(error, f"Windows MCP token ACL {action} failed (Win32 {error})")


def _current_user_sid(advapi, kernel) -> str:
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _failure("OpenProcessToken")
    try:
        required = wintypes.DWORD()
        advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(required))
        if required.value < ctypes.sizeof(_TokenUser):
            raise _failure("GetTokenInformation size")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(
            token, _TOKEN_USER, buffer, required, ctypes.byref(required),
        ):
            raise _failure("GetTokenInformation")
        user_sid = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents.User.Sid
        if not user_sid:
            raise OSError("Windows access token did not contain a user SID")
        sid_string = ctypes.c_void_p()
        if not advapi.ConvertSidToStringSidW(user_sid, ctypes.byref(sid_string)):
            raise _failure("ConvertSidToStringSidW")
        try:
            return ctypes.wstring_at(sid_string)
        finally:
            kernel.LocalFree(sid_string)
    finally:
        kernel.CloseHandle(token)


def _dacl_sddl(handle: int, advapi, kernel) -> str:
    security = ctypes.c_void_p()
    status = advapi.GetSecurityInfo(
        handle, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
        None, None, None, None, ctypes.byref(security),
    )
    if status != 0:
        raise _failure("GetSecurityInfo", status)
    try:
        string_pointer = ctypes.c_void_p()
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            security, _SDDL_REVISION, _DACL_SECURITY_INFORMATION,
            ctypes.byref(string_pointer), None,
        ):
            raise _failure("ConvertSecurityDescriptorToStringSecurityDescriptorW")
        try:
            return ctypes.wstring_at(string_pointer)
        finally:
            kernel.LocalFree(string_pointer)
    finally:
        kernel.LocalFree(security)


def _assert_private_dacl(sddl: str, current_sid: str) -> None:
    # "D:P" = an explicitly protected DACL: no inherited Everyone / Users
    # grants, even if a caller chooses a world-readable parent directory.
    if not sddl.startswith("D:P"):
        raise OSError("Windows MCP token file DACL is not protected")
    entries = re.findall(r"\(([^()]*)\)", sddl)
    expected = {current_sid, "SY", "BA"}
    aliases = {"S-1-5-18": "SY", "S-1-5-32-544": "BA"}
    actual = set()
    if len(entries) != 3:
        raise OSError("Windows MCP token file has unexpected ACL entries")
    for ace in entries:
        fields = ace.split(";")
        if len(fields) != 6 or fields[0] != "A" or fields[1]:
            raise OSError("Windows MCP token file has non-private ACL entries")
        rights = fields[2]
        if rights != "FA" and not (
            rights.startswith("0x") and int(rights, 16) == _FILE_ALL_ACCESS
        ):
            raise OSError("Windows MCP token file ACL does not grant expected rights")
        sid = aliases.get(fields[5], fields[5])
        actual.add(sid)
    if actual != expected:
        raise OSError("Windows MCP token file ACL grants an unexpected principal")


def create_private_windows_staging(path: Path) -> tuple[int, str]:
    """Return (fd, path) for a NEW ACL-restricted temporary file.

    Never write a token until the real DACL has been checked on the open
    filesystem handle. Creating the secured file from birth avoids a
    write-then-chmod exposure window and rejects filesystems without ACLs.
    The caller owns and closes the returned file descriptor.
    """
    if os.name != "nt":
        raise OSError("Windows ACL creation is supported only on Windows")
    import msvcrt

    advapi, kernel = _api()
    user_sid = _current_user_sid(advapi, kernel)
    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;;FA;;;{user_sid})(A;;FA;;;SY)(A;;FA;;;BA)"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, _SDDL_REVISION, ctypes.byref(descriptor), None,
    ):
        raise _failure("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False,
        )
        for _ in range(_MAX_COLLISION_RETRIES):
            staging = path.with_name(f".{path.name}.{secrets.token_hex(12)}")
            handle = kernel.CreateFileW(
                str(staging), _GENERIC_READ_WRITE,
                0,  # exclusive while writing: no other process may open it
                ctypes.byref(attributes), _CREATE_NEW,
                _FILE_ATTRIBUTE_NORMAL, None,
            )
            if handle == _INVALID_HANDLE_VALUE or handle is None:
                error = ctypes.get_last_error()
                if error in (80, 183):  # name already exists; try a fresh one
                    continue
                raise _failure("CreateFileW", error)
            try:
                # Even if the security descriptor was silently ignored by
                # an unusual filesystem, do not write any credential.
                _assert_private_dacl(_dacl_sddl(handle, advapi, kernel), user_sid)
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                return fd, str(staging)
            except BaseException:
                kernel.CloseHandle(handle)
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        raise OSError("unable to create an unused Windows MCP token staging file")
    finally:
        kernel.LocalFree(descriptor)
