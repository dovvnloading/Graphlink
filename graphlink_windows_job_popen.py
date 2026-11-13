"""Windows-only creation-time Job Object attachment for guarded subprocesses.

Python 3.12 subprocess.Popen supports STARTUPINFO.handle_list, but does not
expose PROC_THREAD_ATTRIBUTE_JOB_LIST. Ordinary Popen -> guard.assign(pid)
allows the child to run before the Job Object is attached. This subclass
retains Python's existing stdio/pipe/wait machinery and replaces ONLY the
Windows CreateProcess step with the documented STARTUPINFOEX job-list path.

Do not use this module on non-Windows platforms. Unsupported Win32 attributes
are a hard failure; never silently fall back to unguarded Popen.

Windows API specification:
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
https://devblogs.microsoft.com/oldnewthing/20230209-00/?p=107812
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import warnings
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("Graphlink's atomic Job Object Popen requires Windows")

import _winapi  # Windows-only CPython process/handle API

from graphlink_execution_guard import ExecutionGuardUnavailable

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# PROC_THREAD_ATTRIBUTE_VALUE(number, thread=False, input=True, additive=False)
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
_kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t),
]
_kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
_kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
    ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_size_t),
]
_kernel32.DeleteProcThreadAttributeList.restype = None
_kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
_kernel32.CreateProcessW.restype = wintypes.BOOL
_kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
    ctypes.POINTER(_STARTUPINFOEXW), ctypes.POINTER(_PROCESS_INFORMATION),
]
_kernel32.IsProcessInJob.restype = wintypes.BOOL
_kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _environment_block(env: dict[str, str] | None):
    """Build the UTF-16 Windows environment block while preserving caller isolation."""
    if env is None:
        return None
    pairs = []
    for key, value in env.items():
        key = os.fsdecode(key)
        value = os.fsdecode(value)
        if not key or ("\0" in key) or ("\0" in value):
            raise ValueError("invalid subprocess environment entry")
        if "=" in key and not (key.startswith("=") and key.find("=", 1) > 1):
            raise ValueError("invalid Windows environment variable name")
        pairs.append(f"{key}={value}")
    # Windows requires two terminators for even an empty block. The entries
    # are case-insensitively sorted like CPython's Windows environment path.
    return ctypes.create_unicode_buffer("\0".join(sorted(pairs, key=str.upper)) + "\0\0")


def _create_process_in_job(
    application: str | None,
    command: str,
    cwd: str | None,
    env: dict[str, str] | None,
    creationflags: int,
    startupinfo: subprocess.STARTUPINFO,
    handle_list: list[int],
    job_handle: int,
) -> tuple[int, int, int, int]:
    """Return raw process/thread handles and ids, always in the assigned Job."""
    if not job_handle:
        raise ExecutionGuardUnavailable("Cannot start process without a live Windows Job Object.")

    attr_count = 1 + bool(handle_list)
    required = ctypes.c_size_t()
    # Windows API intentionally returns failure on the sizing call.
    _kernel32.InitializeProcThreadAttributeList(None, attr_count, 0, ctypes.byref(required))
    if not required.value:
        raise ctypes.WinError(ctypes.get_last_error())

    memory = ctypes.create_string_buffer(required.value)
    attributes = ctypes.cast(memory, ctypes.c_void_p)
    if not _kernel32.InitializeProcThreadAttributeList(
        attributes, attr_count, 0, ctypes.byref(required),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    proc_info = _PROCESS_INFORMATION()
    try:
        jobs = (wintypes.HANDLE * 1)(job_handle)
        if not _kernel32.UpdateProcThreadAttribute(
            attributes, 0, _PROC_THREAD_ATTRIBUTE_JOB_LIST,
            ctypes.cast(jobs, ctypes.c_void_p), ctypes.sizeof(jobs), None, None,
        ):
            raise ExecutionGuardUnavailable(
                f"Windows Job Object creation-time attachment unavailable (Win32 {ctypes.get_last_error()}); "
                "refusing unguarded execution."
            )

        if handle_list:
            handles = (wintypes.HANDLE * len(handle_list))(*handle_list)
            if not _kernel32.UpdateProcThreadAttribute(
                attributes, 0, _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(handles, ctypes.c_void_p), ctypes.sizeof(handles), None, None,
            ):
                raise ExecutionGuardUnavailable(
                    f"Windows child stdio handle allowlist setup failed ({ctypes.get_last_error()}); "
                    "refusing unguarded execution."
                )

        info = _STARTUPINFOEXW()
        info.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        info.StartupInfo.dwFlags = startupinfo.dwFlags
        info.StartupInfo.wShowWindow = startupinfo.wShowWindow
        if startupinfo.dwFlags & _winapi.STARTF_USESTDHANDLES:
            stdin_handle = startupinfo.hStdInput
            stdout_handle = startupinfo.hStdOutput
            stderr_handle = startupinfo.hStdError
            if stdin_handle is None or stdout_handle is None or stderr_handle is None:
                raise ExecutionGuardUnavailable(
                    "Windows child standard stream handle missing; refusing unguarded execution."
                )
            info.StartupInfo.hStdInput = int(stdin_handle)
            info.StartupInfo.hStdOutput = int(stdout_handle)
            info.StartupInfo.hStdError = int(stderr_handle)
        info.lpAttributeList = attributes
        command_buffer = ctypes.create_unicode_buffer(command)
        environment = _environment_block(env)
        success = _kernel32.CreateProcessW(
            application, command_buffer, None, None, bool(handle_list),
            creationflags | _EXTENDED_STARTUPINFO_PRESENT
            | (_CREATE_UNICODE_ENVIRONMENT if environment is not None else 0),
            ctypes.cast(environment, ctypes.c_void_p) if environment is not None else None,
            cwd, ctypes.byref(info), ctypes.byref(proc_info),
        )
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

        # Verify the *specific* Job Object, not merely "in any job":
        # CI runners may themselves be assigned to unrelated job objects.
        member = wintypes.BOOL()
        try:
            if not _kernel32.IsProcessInJob(proc_info.hProcess, job_handle, ctypes.byref(member)):
                raise ExecutionGuardUnavailable(
                    f"Could not verify child Job Object membership ({ctypes.get_last_error()})."
                )
            if not member.value:
                raise ExecutionGuardUnavailable("Created child was not assigned to the requested Job Object.")
        except BaseException:
            _kernel32.TerminateProcess(proc_info.hProcess, 1)
            _kernel32.WaitForSingleObject(proc_info.hProcess, 5000)
            _kernel32.CloseHandle(proc_info.hThread)
            _kernel32.CloseHandle(proc_info.hProcess)
            raise
        return (
            int(proc_info.hProcess), int(proc_info.hThread),
            int(proc_info.dwProcessId), int(proc_info.dwThreadId),
        )
    finally:
        _kernel32.DeleteProcThreadAttributeList(attributes)


class WindowsJobPopen(subprocess.Popen):
    """Popen-compatible Windows process, born in a pre-configured Job Object."""

    def __init__(self, args, *, guard, **kwargs):
        self._graphlink_atomic_job = False
        self._graphlink_guard = guard
        super().__init__(args, **kwargs)

    def _execute_child(
        self, args, executable, preexec_fn, close_fds, pass_fds, cwd, env,
        startupinfo, creationflags, shell, p2cread, p2cwrite, c2pread,
        c2pwrite, errread, errwrite, unused_restore_signals, unused_gid,
        unused_gids, unused_uid, unused_umask, unused_start_new_session,
        unused_process_group,
    ):
        # Mirror CPython 3.12's Windows _execute_child handling of pipes,
        # quoting, shell, and inherited handle-list creation. The only
        # changed operation is CreateProcessW with the Job Object attribute.
        if pass_fds or preexec_fn is not None:
            raise ValueError("pass_fds/preexec_fn unsupported by Windows guarded Popen")
        if isinstance(args, str):
            pass
        elif isinstance(args, bytes):
            if shell:
                raise TypeError("bytes args cannot be used with shell=True")
            args = subprocess.list2cmdline([args])
        elif isinstance(args, os.PathLike):
            if shell:
                raise TypeError("path-like command cannot use shell=True")
            args = subprocess.list2cmdline([args])
        else:
            args = subprocess.list2cmdline(args)
        if executable is not None:
            executable = os.fsdecode(executable)

        startupinfo = subprocess.STARTUPINFO() if startupinfo is None else startupinfo.copy()
        unexpected = set(startupinfo.lpAttributeList or {}) - {"handle_list"}
        if unexpected:
            raise ExecutionGuardUnavailable(
                f"Unsupported Windows startup attributes {sorted(unexpected)}; refusing unguarded execution."
            )
        use_std_handles = -1 not in (p2cread, c2pwrite, errwrite)
        if use_std_handles:
            startupinfo.dwFlags |= _winapi.STARTF_USESTDHANDLES
            startupinfo.hStdInput = p2cread
            startupinfo.hStdOutput = c2pwrite
            startupinfo.hStdError = errwrite

        attr = startupinfo.lpAttributeList or {}
        handles = list(attr.get("handle_list", []))
        if use_std_handles:
            handles += [int(p2cread), int(c2pwrite), int(errwrite)]
        handles = self._filter_handle_list(handles)
        if handles and not close_fds:
            warnings.warn(
                "explicit guarded handle_list restricts inheritance even with close_fds=False",
                RuntimeWarning, stacklevel=2,
            )

        if shell:
            startupinfo.dwFlags |= _winapi.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = _winapi.SW_HIDE
            if not executable:
                comspec = os.environ.get("ComSpec")
                if not comspec:
                    system_root = os.environ.get("SystemRoot", "")
                    comspec = os.path.join(system_root, "System32", "cmd.exe")
                    if not os.path.isabs(comspec):
                        raise FileNotFoundError("shell not found: neither ComSpec nor SystemRoot set")
                if os.path.isabs(comspec):
                    executable = comspec
            else:
                comspec = executable
            args = '{} /c "{}"'.format(comspec, args)

        if cwd is not None:
            cwd = os.fsdecode(cwd)

        sys.audit("subprocess.Popen", executable, args, cwd, env)
        try:
            job_handle = self._graphlink_guard.duplicate_native_handle()
            try:
                hp, ht, pid, _tid = _create_process_in_job(
                    executable, args, cwd, env, creationflags,
                    startupinfo, handles, job_handle,
                )
            finally:
                # This duplicate keeps the job alive throughout CreateProcess
                # even if another thread closes the guard meanwhile.
                _kernel32.CloseHandle(job_handle)
        finally:
            # Also close child-facing pipe handles if obtaining the Job
            # Object duplicate failed *before* CreateProcess was attempted.
            self._close_pipe_fds(
                p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite,
            )
        self._child_created = True
        self._handle = subprocess.Handle(hp)
        self.pid = pid
        _winapi.CloseHandle(ht)
        self._graphlink_atomic_job = True
