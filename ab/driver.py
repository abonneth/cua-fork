"""A cua-driver daemon started the way a GUI host starts it, a client for its pipe, and a watcher for new top-level windows."""

import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import subprocess
import threading
import time

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
ENV = {**os.environ, "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.restype = wt.HANDLE


def exe_name(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return "?"
    finally:
        kernel32.CloseHandle(handle)


def visible_windows() -> dict[int, tuple[str, int]]:
    found: dict[int, tuple[str, int]] = {}

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, 256)
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found[int(hwnd)] = (name.value, pid.value)
        return True

    user32.EnumWindows(ENUM(visit), 0)
    return found


class Watcher:
    """Every top-level window that turns visible while it runs, polled every 5 ms."""

    def __init__(self) -> None:
        self.baseline = set(visible_windows())
        self.seen: dict[int, str] = {}
        self.running = True
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.thread.start()

    def poll(self) -> None:
        while self.running:
            for hwnd, (cls, pid) in visible_windows().items():
                if hwnd not in self.baseline and hwnd not in self.seen:
                    self.seen[hwnd] = f"{cls} ({exe_name(pid)})"
            time.sleep(0.005)

    def stop(self) -> list[str]:
        self.running = False
        self.thread.join()
        return sorted(self.seen.values())


def consoles(windows: list[str]) -> list[str]:
    return [w for w in windows if w.split(" ", 1)[0] in CONSOLE_CLASSES]


class Daemon:
    """`cua-driver serve` with no console of its own, as a GUI host (an Electron app, a service) starts it."""

    def __init__(self, exe: str, pipe: str) -> None:
        self.pipe = pipe
        self.process = subprocess.Popen(
            [exe, "serve", "--socket", pipe],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=ENV,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                self.request({"method": "list"})
                return
            except OSError:
                if self.process.poll() is not None:
                    raise RuntimeError(f"daemon exited with {self.process.returncode}")
                time.sleep(0.2)
        raise TimeoutError("daemon never served its pipe")

    def request(self, payload: dict) -> dict:
        with open(self.pipe, "r+b", buffering=0) as handle:
            handle.write((json.dumps(payload) + "\n").encode())
            return json.loads(io.BufferedReader(handle).readline() or b"{}")

    def call(self, tool: str, args: dict) -> dict:
        return self.request({"method": "call", "name": tool, "args": args})

    def stop(self) -> None:
        try:
            self.request({"method": "shutdown"})
        except OSError:
            pass
        try:
            self.process.wait(15)
        except subprocess.TimeoutExpired:
            self.process.kill()
