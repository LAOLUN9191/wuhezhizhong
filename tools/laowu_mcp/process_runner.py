from __future__ import annotations

import subprocess
import sys
import threading
from typing import BinaryIO


class _WindowsChild:
    def __init__(self, process_handle: int, pid: int, stdin: BinaryIO, stdout: BinaryIO, stderr: BinaryIO):
        self._process_handle = process_handle
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

    def wait(self) -> int:
        import _winapi

        _winapi.WaitForSingleObject(self._process_handle, _winapi.INFINITE)
        result = _winapi.GetExitCodeProcess(self._process_handle)
        _winapi.CloseHandle(self._process_handle)
        self._process_handle = 0
        return result - 2**32 if result >= 2**31 else result

    def kill(self) -> None:
        import _winapi

        _winapi.TerminateProcess(self._process_handle, 1)


def _spawn_windows_child(command: list[str]) -> tuple[_WindowsChild, int]:
    import _winapi
    import ctypes
    import ctypes.wintypes
    import msvcrt
    import os

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle_type = ctypes.wintypes.HANDLE
    dword_type = ctypes.wintypes.DWORD

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", dword_type),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", dword_type),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", dword_type),
            ("SchedulingClass", dword_type),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def check(ok: int) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = handle_type
    kernel32.SetInformationJobObject.argtypes = [handle_type, ctypes.c_int, ctypes.c_void_p, dword_type]
    kernel32.SetInformationJobObject.restype = ctypes.wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [handle_type, handle_type]
    kernel32.AssignProcessToJobObject.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [handle_type]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [handle_type, dword_type]
    kernel32.TerminateProcess.restype = ctypes.wintypes.BOOL
    kernel32.ResumeThread.argtypes = [handle_type]
    kernel32.ResumeThread.restype = dword_type

    job_handle = kernel32.CreateJobObjectW(None, None)
    if not job_handle:
        check(0)
    pipe_handles: list[int] = []
    process_handle = thread_handle = 0
    parent_streams: list[BinaryIO] = []
    try:
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        check(kernel32.SetInformationJobObject(
            job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
        ))

        stdin_read, stdin_write = _winapi.CreatePipe(None, 0)
        stdout_read, stdout_write = _winapi.CreatePipe(None, 0)
        stderr_read, stderr_write = _winapi.CreatePipe(None, 0)
        pipe_handles.extend((stdin_read, stdin_write, stdout_read, stdout_write, stderr_read, stderr_write))
        for handle in (stdin_read, stdout_write, stderr_write):
            os.set_handle_inheritable(handle, True)
        for handle in (stdin_write, stdout_read, stderr_read):
            os.set_handle_inheritable(handle, False)

        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= _winapi.STARTF_USESTDHANDLES
        startup.hStdInput = stdin_read
        startup.hStdOutput = stdout_write
        startup.hStdError = stderr_write
        startup.lpAttributeList = {"handle_list": [int(stdin_read), int(stdout_write), int(stderr_write)]}
        process_handle, thread_handle, pid, _ = _winapi.CreateProcess(
            None, subprocess.list2cmdline(command), None, None, True,
            0x00000004, None, None, startup,  # CREATE_SUSPENDED
        )
        for handle in (stdin_read, stdout_write, stderr_write):
            _winapi.CloseHandle(handle)
            pipe_handles.remove(handle)

        check(kernel32.AssignProcessToJobObject(job_handle, process_handle))
        if kernel32.ResumeThread(thread_handle) == 0xFFFFFFFF:
            check(0)
        _winapi.CloseHandle(thread_handle)
        thread_handle = 0

        stdin = os.fdopen(msvcrt.open_osfhandle(int(stdin_write), os.O_WRONLY | os.O_BINARY), "wb", buffering=0)
        pipe_handles.remove(stdin_write)
        parent_streams.append(stdin)
        stdout = os.fdopen(msvcrt.open_osfhandle(int(stdout_read), os.O_RDONLY | os.O_BINARY), "rb", buffering=0)
        pipe_handles.remove(stdout_read)
        parent_streams.append(stdout)
        stderr = os.fdopen(msvcrt.open_osfhandle(int(stderr_read), os.O_RDONLY | os.O_BINARY), "rb", buffering=0)
        pipe_handles.remove(stderr_read)
        parent_streams.append(stderr)
        return _WindowsChild(process_handle, pid, stdin, stdout, stderr), job_handle
    except BaseException:
        if process_handle:
            kernel32.TerminateProcess(process_handle, 1)
            _winapi.WaitForSingleObject(process_handle, _winapi.INFINITE)
            _winapi.CloseHandle(process_handle)
        if thread_handle:
            _winapi.CloseHandle(thread_handle)
        for stream in parent_streams:
            stream.close()
        for handle in pipe_handles:
            _winapi.CloseHandle(handle)
        kernel32.CloseHandle(job_handle)
        raise


def _forward(source: BinaryIO, target: BinaryIO) -> None:
    target_open = True
    while True:
        try:
            chunk = source.read(65536)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        if target_open:
            try:
                target.write(chunk)
                target.flush()
            except (BrokenPipeError, OSError, ValueError):
                target_open = False


def _forward_input(source: BinaryIO, target: BinaryIO) -> None:
    try:
        while True:
            chunk = source.read(65536)
            if not chunk:
                return
            target.write(chunk)
            target.flush()
    except (BrokenPipeError, OSError, ValueError):
        return
    finally:
        try:
            target.close()
        except OSError:
            pass


def run(command: list[str]) -> int:
    if not command:
        return 2
    job_handle = 0
    if sys.platform == "win32" and command[0].lower().endswith((".cmd", ".bat")):
        print(
            "Unable to start Codex child: Windows .cmd/.bat launchers are unsupported; "
            "set CODEX_CLI_PATH to the actual codex.exe executable.",
            file=sys.stderr,
        )
        return 70
    try:
        if sys.platform == "win32":
            child, job_handle = _spawn_windows_child(command)
        else:
            child = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0,
            )
    except OSError as exc:
        print(f"Unable to start Codex child: {exc}", file=sys.stderr)
        return 70

    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    threads = [
        threading.Thread(target=_forward, args=(child.stdout, sys.stdout.buffer)),
        threading.Thread(target=_forward, args=(child.stderr, sys.stderr.buffer)),
        threading.Thread(target=_forward_input, args=(sys.stdin.buffer, child.stdin)),
    ]
    started_threads: list[threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
    except Exception as exc:
        cleanup_errors: list[str] = []
        job_closed = False
        if job_handle:
            try:
                import _winapi

                _winapi.CloseHandle(job_handle)
                job_handle = 0
                job_closed = True
            except OSError as cleanup_exc:
                cleanup_errors.append(f"Unable to close process job: {cleanup_exc}")
        if not job_closed:
            try:
                child.kill()
            except OSError as cleanup_exc:
                cleanup_errors.append(f"Unable to terminate child process: {cleanup_exc}")
        if job_handle:
            try:
                import _winapi

                _winapi.CloseHandle(job_handle)
                job_handle = 0
            except OSError as cleanup_exc:
                cleanup_errors.append(f"Unable to close process job: {cleanup_exc}")
        try:
            child.stdin.close()
        except (OSError, ValueError) as cleanup_exc:
            cleanup_errors.append(f"Unable to close child input: {cleanup_exc}")
        try:
            child.wait()
        except (OSError, ValueError) as cleanup_exc:
            cleanup_errors.append(f"Unable to wait for child process cleanup: {cleanup_exc}")
        for thread in started_threads:
            thread.join()
        for stream in (child.stdin, child.stdout, child.stderr):
            try:
                stream.close()
            except OSError as cleanup_exc:
                cleanup_errors.append(f"Unable to close child stream: {cleanup_exc}")
        print(f"Unable to start process forwarding threads: {exc}", file=sys.stderr)
        for cleanup_error in cleanup_errors:
            print(cleanup_error, file=sys.stderr)
        return 70
    try:
        result = child.wait()
        for thread in threads:
            thread.join()
    finally:
        if job_handle:
            import _winapi

            _winapi.CloseHandle(job_handle)
    return result


def main() -> int:
    try:
        separator = sys.argv.index("--")
    except ValueError:
        return 2
    return run(sys.argv[separator + 1:])


if __name__ == "__main__":
    raise SystemExit(main())
