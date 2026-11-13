"""Capture a hidden terminal so CLI tools retain their live progress output."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Iterator


class TerminalProcess:
    """A real terminal, without a visible console or additional dependencies."""

    def __init__(self, command: list[str]):
        self._process = None
        self._stream = None
        self._env = os.environ.copy()
        self._env["TERM"] = "xterm-256color"
        self._env.pop("UV_NO_PROGRESS", None)
        # uv deliberately suppresses all progress when Rust logging is enabled.
        self._env.pop("RUST_LOG", None)
        self._env.pop("NO_COLOR", None)
        self._env["CLICOLOR_FORCE"] = "1"
        self._env["FORCE_COLOR"] = "1"
        self._env.pop("JPY_SESSION_NAME", None)
        if os.name == "nt":
            self._start_windows(command)
        else:
            import fcntl
            import pty
            import struct
            import termios

            master, slave = pty.openpty()
            try:
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 120, 180, 0, 0))
                self._process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                                 env=self._env)
            except BaseException:
                os.close(master)
                raise
            finally:
                os.close(slave)
            self._stream = os.fdopen(master, "rb", buffering=0)

    def _start_windows(self, command: list[str]) -> None:
        import ctypes as c
        import msvcrt
        from ctypes import wintypes as w

        class Coord(c.Structure):
            _fields_ = [("X", c.c_short), ("Y", c.c_short)]

        class StartupInfo(c.Structure):
            _fields_ = [
                ("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD),
                ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", c.POINTER(w.BYTE)), ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE),
            ]

        class StartupInfoEx(c.Structure):
            _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", c.c_void_p)]

        class ProcessInfo(c.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.CreatePipe.argtypes = [c.POINTER(w.HANDLE), c.POINTER(w.HANDLE), c.c_void_p, w.DWORD]
        kernel.CreatePipe.restype = w.BOOL
        kernel.CreatePseudoConsole.argtypes = [Coord, w.HANDLE, w.HANDLE, w.DWORD, c.POINTER(c.c_void_p)]
        kernel.CreatePseudoConsole.restype = c.c_long
        kernel.ClosePseudoConsole.argtypes = [c.c_void_p]
        kernel.ClosePseudoConsole.restype = None
        kernel.InitializeProcThreadAttributeList.argtypes = [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)]
        kernel.InitializeProcThreadAttributeList.restype = w.BOOL
        kernel.UpdateProcThreadAttribute.argtypes = [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p,
                                                    c.c_size_t, c.c_void_p, c.c_void_p]
        kernel.UpdateProcThreadAttribute.restype = w.BOOL
        kernel.DeleteProcThreadAttributeList.argtypes = [c.c_void_p]
        kernel.DeleteProcThreadAttributeList.restype = None
        kernel.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL,
                                          w.DWORD, c.c_void_p, w.LPCWSTR,
                                          c.POINTER(StartupInfoEx), c.POINTER(ProcessInfo)]
        kernel.CreateProcessW.restype = w.BOOL
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
        kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        self._kernel = kernel
        self._info = ProcessInfo()
        handles = [w.HANDLE() for _ in range(4)]
        pseudo = c.c_void_p()
        attributes = None
        initialized = False
        try:
            if not kernel.CreatePipe(c.byref(handles[0]), c.byref(handles[1]), None, 0):
                raise c.WinError(c.get_last_error())
            if not kernel.CreatePipe(c.byref(handles[2]), c.byref(handles[3]), None, 0):
                raise c.WinError(c.get_last_error())
            result = kernel.CreatePseudoConsole(Coord(180, 120), handles[0], handles[3], 0, c.byref(pseudo))
            if result < 0:
                raise OSError(f"CreatePseudoConsole failed: 0x{result & 0xffffffff:08x}")
            size = c.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
            attributes = c.create_string_buffer(size.value)
            if not kernel.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)):
                raise c.WinError(c.get_last_error())
            initialized = True
            if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x20016, pseudo,
                                                    c.sizeof(pseudo), None, None):
                raise c.WinError(c.get_last_error())
            startup = StartupInfoEx()
            startup.StartupInfo.cb = c.sizeof(startup)
            # Null standard handles let ConPTY supply console handles instead of
            # inheriting redirected stdout/stderr from the GUI or its launcher.
            startup.StartupInfo.dwFlags = 0x101  # USESTDHANDLES | USESHOWWINDOW
            startup.lpAttributeList = c.cast(attributes, c.c_void_p)
            args = c.create_unicode_buffer(subprocess.list2cmdline(command))
            environment = c.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in
                                                            sorted(self._env.items())) + "\0\0")
            if not kernel.CreateProcessW(None, args, None, None, False, 0x80400, environment, None,
                                         c.byref(startup), c.byref(self._info)):
                raise c.WinError(c.get_last_error())
            fd = msvcrt.open_osfhandle(handles[2].value, os.O_RDONLY | os.O_BINARY)
            handles[2] = w.HANDLE()  # The Python stream now owns this handle.
            self._stream = os.fdopen(fd, "rb", buffering=0)
        except BaseException:
            if self._info.hProcess:
                kernel.TerminateProcess(self._info.hProcess, 1)
                kernel.WaitForSingleObject(self._info.hProcess, 0xffffffff)
                kernel.CloseHandle(self._info.hProcess)
                kernel.CloseHandle(self._info.hThread)
            if pseudo.value:
                kernel.ClosePseudoConsole(pseudo)
            for handle in handles:
                if handle.value:
                    kernel.CloseHandle(handle)
            raise
        finally:
            if initialized:
                kernel.DeleteProcThreadAttributeList(attributes)
        kernel.CloseHandle(handles[0])
        kernel.CloseHandle(handles[3])
        self._returncode = 1

        def finish() -> None:
            kernel.WaitForSingleObject(self._info.hProcess, 0xffffffff)
            code = w.DWORD()
            kernel.GetExitCodeProcess(self._info.hProcess, c.byref(code))
            self._returncode = code.value
            # Close on a separate thread while the reader drains the console pipe.
            # Closing on the reader thread can deadlock on a full output buffer.
            kernel.ClosePseudoConsole(pseudo)
            kernel.CloseHandle(handles[1])

        self._waiter = threading.Thread(target=finish, daemon=True)
        self._waiter.start()

    def output(self, *, heartbeat: float | None = None) -> Iterator[bytes]:
        """Yield bytes, or an empty heartbeat while a silent child is still running."""
        import errno

        next_heartbeat = time.monotonic() + (heartbeat or 0)
        while True:
            available = 65536
            if heartbeat is not None:
                if os.name == "nt":
                    import ctypes as c
                    import msvcrt
                    from ctypes import wintypes as w

                    kernel = self._kernel
                    kernel.PeekNamedPipe.argtypes = [w.HANDLE, c.c_void_p, w.DWORD,
                                                     c.c_void_p, c.POINTER(w.DWORD), c.c_void_p]
                    kernel.PeekNamedPipe.restype = w.BOOL
                    count = w.DWORD()
                    if not kernel.PeekNamedPipe(msvcrt.get_osfhandle(self._stream.fileno()),
                                                None, 0, None, c.byref(count), None):
                        error = c.get_last_error()
                        if error in (109, 232):
                            break
                        raise c.WinError(error)
                    available = min(65536, count.value)
                    if not available:
                        time.sleep(0.05)
                else:
                    import select

                    ready, _, _ = select.select([self._stream], [], [], min(0.05, heartbeat))
                    available = 65536 if ready else 0
                if not available:
                    if time.monotonic() >= next_heartbeat:
                        next_heartbeat = time.monotonic() + heartbeat
                        yield b""
                    continue
            try:
                data = self._stream.read(available)
            except OSError as exc:
                if os.name != "nt" and exc.errno == errno.EIO:
                    break  # POSIX PTYs report EIO when the slave closes.
                raise
            if not data:
                break
            yield data
            next_heartbeat = time.monotonic() + (heartbeat or 0)

    def wait(self) -> int:
        if self._process is not None:
            return self._process.wait()
        self._waiter.join()
        return self._returncode

    def close(self) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
            self._process.wait()
        else:
            if self._waiter.is_alive():
                self._kernel.TerminateProcess(self._info.hProcess, 1)
            # Keep draining while ClosePseudoConsole finishes.
            for _ in self.output():
                pass
            self._waiter.join()
            self._kernel.CloseHandle(self._info.hThread)
            self._kernel.CloseHandle(self._info.hProcess)
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
