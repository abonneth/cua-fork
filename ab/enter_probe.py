"""Which Edge setup-page steps take the foreground when a background process drives them, per input route."""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time
from multiprocessing.connection import Client, Listener

import uiautomation as auto

sys.stdout.reconfigure(encoding="utf-8")
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetForegroundWindow.restype = wt.HWND

ADDRESS = ("127.0.0.1", 47613)
URL = "edge://inspect/#remote-debugging"
REPS = 3
WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_COMMAND, VK_RETURN = 0x100, 0x101, 0x102, 0x111, 0x0D
IDC_NEW_TAB, IDC_CLOSE_TAB = 34014, 34015


def pattern(control: auto.Control, name: str):
    try:
        return control.GetPattern(getattr(auto.PatternId, f"{name}Pattern"))
    except Exception:
        return None


def edge() -> auto.Control:
    wins = [w for w in auto.GetRootControl().GetChildren() if w.ClassName == "Chrome_WidgetWin_1" and "Edge" in w.Name]
    return max(wins, key=lambda w: w.BoundingRectangle.width() * w.BoundingRectangle.height())


def tabs(win: auto.Control) -> list[auto.Control]:
    return [c for c, _ in auto.WalkControl(win, maxDepth=12) if c.ControlTypeName == "TabItemControl"]


def selected_tab(win: auto.Control) -> auto.Control:
    return next(t for t in tabs(win) if pattern(t, "SelectionItem").IsSelected)


def omnibox(win: auto.Control) -> auto.Control:
    return win.EditControl(Name="Address and search bar")


def on_setup_page(win: auto.Control) -> bool:
    for c, _ in auto.WalkControl(win, maxDepth=14):
        if c.ControlTypeName == "DocumentControl" and (p := pattern(c, "Value")) is not None and p.Value.startswith("edge://inspect"):
            return True
    return False


def checkbox(win: auto.Control) -> auto.Control | None:
    for c, _ in auto.WalkControl(win, maxDepth=16):
        if c.ControlTypeName == "CheckBoxControl" and pattern(c, "Toggle") is not None:
            return c
    return None


def wait(cond, seconds: float = 4.0) -> bool:
    deadline = time.monotonic() + seconds
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.1)
    return bool(cond())


def post(hwnd: int, msg: int, wparam: int, lparam: int = 0) -> None:
    user32.PostMessageW(hwnd, msg, wparam, lparam)


def post_enter(hwnd: int) -> None:
    post(hwnd, WM_KEYDOWN, VK_RETURN, 0x001C0001)
    post(hwnd, WM_CHAR, 0x0D, 0x001C0001)
    post(hwnd, WM_KEYUP, VK_RETURN, 0xC01C0001)


class Worker:
    """Runs one setup step per request; lives in a process that never owns the foreground."""

    def __init__(self) -> None:
        self.win = edge()
        self.before = len(tabs(self.win))

    def run(self, step: str) -> bool:
        win, hwnd = self.win, self.win.NativeWindowHandle
        if step == "reset":
            self.win = edge()
            self.before = len(tabs(self.win))
            return True
        if step == "uia new tab":
            pattern(win.ButtonControl(Name="New Tab"), "Invoke").Invoke()
            return wait(lambda: len(tabs(win)) == self.before + 1)
        if step == "WM_COMMAND new tab":
            post(hwnd, WM_COMMAND, IDC_NEW_TAB)
            return wait(lambda: len(tabs(win)) == self.before + 1)
        if step == "uia SetValue url":
            pattern(omnibox(win), "Value").SetValue(URL)
            return wait(lambda: pattern(omnibox(win), "Value").Value == URL, 2)
        if step == "WM_CHAR type url":
            for ch in URL:
                post(hwnd, WM_CHAR, ord(ch), 1)
            return wait(lambda: pattern(omnibox(win), "Value").Value == URL, 2)
        if step == "PostMessage Enter":
            post_enter(hwnd)
            return wait(lambda: on_setup_page(win))
        if step in ("uia Toggle on", "uia Toggle off"):
            if not wait(lambda: checkbox(win) is not None, 6):
                return False
            box = checkbox(win)
            pattern(box, "Toggle").Toggle()
            want = 1 if step.endswith("on") else 0
            return wait(lambda: pattern(checkbox(win), "Toggle").ToggleState == want, 2)
        if step == "uia close tab":
            pattern(selected_tab(win).ButtonControl(Name="Close tab"), "Invoke").Invoke()
            return wait(lambda: len(tabs(win)) == self.before)
        if step == "WM_COMMAND close tab":
            post(hwnd, WM_COMMAND, IDC_CLOSE_TAB)
            return wait(lambda: len(tabs(win)) == self.before)
        raise ValueError(step)


def worker() -> None:
    auto.SetGlobalSearchTimeout(3)
    state = Worker()
    with Listener(ADDRESS) as listener, listener.accept() as conn:
        while (step := conn.recv()) != "quit":
            try:
                conn.send((state.run(step), ""))
            except Exception as error:
                conn.send((False, f"{type(error).__name__}: {error}"))


def host_window() -> int:
    ready, box = threading.Event(), {}

    def pump() -> None:
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        box["hwnd"] = user32.CreateWindowExW(0, "Edit", "host app", 0x10CF0000, 0, 470, 380, 220, None, None, None, None)
        ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=pump, daemon=True).start()
    ready.wait()
    return box["hwnd"]


def front(hwnd: int) -> bool:
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, fg_thread, True)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.AttachThreadInput(me, fg_thread, False)
    time.sleep(0.4)
    return user32.GetForegroundWindow() == hwnd


def edge_pids() -> set[int]:
    out = subprocess.run(["tasklist", "/fi", "imagename eq msedge.exe", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
    return {int(line.split('","')[1]) for line in out.splitlines() if line.count('","') >= 1}


class EdgeForeground:
    """Ms an Edge window held the foreground while it ran, polled every 2 ms."""

    def __init__(self, pids: set[int]) -> None:
        self.pids, self.ms, self.running = pids, 0.0, True
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.thread.start()

    def poll(self) -> None:
        last = time.monotonic()
        while self.running:
            now = time.monotonic()
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
            if pid.value in self.pids:
                self.ms += (now - last) * 1000
            last = now
            time.sleep(0.002)

    def stop(self) -> int:
        time.sleep(1.0)
        self.running = False
        self.thread.join()
        return round(self.ms)


ROUTES = {
    "UIA": ["uia new tab", "uia SetValue url", "PostMessage Enter", "uia Toggle on", "uia Toggle off", "uia close tab"],
    "window messages": ["WM_COMMAND new tab", "WM_CHAR type url", "PostMessage Enter", "uia Toggle on", "uia Toggle off", "WM_COMMAND close tab"],
}


def probe() -> None:
    host = host_window()
    pids = edge_pids()
    conn = Client(ADDRESS)
    results: dict[tuple[str, str], list[int]] = {}
    for route, steps in ROUTES.items():
        for rep in range(1, REPS + 1):
            conn.send("reset")
            conn.recv()
            print(f"{route} rep {rep}")
            for step in steps:
                fronted = front(host)
                watch = EdgeForeground(pids)
                conn.send(step)
                ok, error = conn.recv()
                ms = watch.stop()
                print(f"  {step}: ok={ok} host_fronted={fronted} edge_foreground_ms={ms} {error}")
                done, stole = results.setdefault((route, step), [0, 0])
                results[(route, step)] = [done + ok, stole + (ms > 0)]
                if not ok and step.endswith("new tab"):
                    break
    conn.send("quit")
    lines = ["| route | step | worked | Edge took the foreground |", "|---|---|---|---|"]
    lines += [f"| {route} | {step} | {d}/{REPS} | {s}/{REPS} |" for (route, step), (d, s) in results.items()]
    summary = "\n".join(lines)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
        out.write(summary + "\n")


worker() if sys.argv[1:] == ["worker"] else probe()
