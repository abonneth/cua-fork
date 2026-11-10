"""Can Edge's omnibox navigate to an internal page, and its tab close, without taking the foreground, per method."""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time

import uiautomation as auto

sys.stdout.reconfigure(encoding="utf-8")
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = wt.LPARAM
user32.GetForegroundWindow.restype = wt.HWND

URL = "edge://inspect/#remote-debugging"
REPS = 3
WM_KEYDOWN, WM_KEYUP, WM_CHAR, VK_RETURN = 0x100, 0x101, 0x102, 0x0D
ENTER_DOWN, ENTER_UP = 0x001C0001, 0xC01C0001


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


HOST = host_window()


def front_host() -> bool:
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(0.2)
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, fg_thread, True)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.BringWindowToTop(HOST)
    user32.SetForegroundWindow(HOST)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.AttachThreadInput(me, fg_thread, False)
    time.sleep(0.5)
    return user32.GetForegroundWindow() == HOST


class Foreground:
    """Total ms the foreground spent away from the host while it ran, polled every 2 ms."""

    def __init__(self) -> None:
        self.away_ms = 0.0
        self.running = True
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.thread.start()

    def poll(self) -> None:
        last = time.monotonic()
        while self.running:
            now = time.monotonic()
            if user32.GetForegroundWindow() != HOST:
                self.away_ms += (now - last) * 1000
            last = now
            time.sleep(0.002)

    def stop(self) -> int:
        time.sleep(1.0)
        self.running = False
        self.thread.join()
        return round(self.away_ms)


def edge() -> auto.Control:
    wins = [w for w in auto.GetRootControl().GetChildren() if w.ClassName == "Chrome_WidgetWin_1" and "Edge" in w.Name]
    return max(wins, key=lambda w: w.BoundingRectangle.width() * w.BoundingRectangle.height())


def tabs(win: auto.Control) -> list[auto.Control]:
    return [c for c, _ in auto.WalkControl(win, maxDepth=12) if c.ControlTypeName == "TabItemControl"]


def selected_tab(win: auto.Control) -> auto.Control:
    return next(t for t in tabs(win) if t.GetSelectionItemPattern().IsSelected)


def omnibox(win: auto.Control) -> auto.Control:
    return win.EditControl(Name="Address and search bar")


def on_setup_page(win: auto.Control) -> bool:
    for c, _ in auto.WalkControl(win, maxDepth=14):
        if c.ControlTypeName == "DocumentControl":
            try:
                if c.GetValuePattern().Value.startswith("edge://inspect"):
                    return True
            except Exception:
                pass
    return False


def post_enter(win: auto.Control, omni: auto.Control) -> None:
    hwnd = win.NativeWindowHandle
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, ENTER_DOWN)
    user32.PostMessageW(hwnd, WM_CHAR, 0x0D, ENTER_DOWN)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, ENTER_UP)


def send_enter(win: auto.Control, omni: auto.Control) -> None:
    hwnd = win.NativeWindowHandle
    user32.SendMessageW(hwnd, WM_KEYDOWN, VK_RETURN, ENTER_DOWN)
    user32.SendMessageW(hwnd, WM_CHAR, 0x0D, ENTER_DOWN)
    user32.SendMessageW(hwnd, WM_KEYUP, VK_RETURN, ENTER_UP)


def uia_focus_then_post_enter(win: auto.Control, omni: auto.Control) -> None:
    omni.SetFocus()
    post_enter(win, omni)


def omnibox_default_action(win: auto.Control, omni: auto.Control) -> None:
    omni.GetLegacyIAccessiblePattern().DoDefaultAction()


def popup_rows() -> list[auto.Control]:
    rows = []
    for w in auto.GetRootControl().GetChildren():
        if w.ClassName != "Chrome_WidgetWin_1":
            continue
        for c, _ in auto.WalkControl(w, maxDepth=12):
            if c.ControlTypeName == "ListItemControl" and URL.split("#")[0].rstrip("/") in (c.Name or ""):
                rows.append(c)
    return rows


def dump_popup() -> None:
    for w in auto.GetRootControl().GetChildren():
        if w.ClassName != "Chrome_WidgetWin_1":
            continue
        for c, depth in auto.WalkControl(w, maxDepth=12):
            if c.ControlTypeName in ("ListControl", "ListItemControl"):
                pats = [p for p in ("Invoke", "SelectionItem", "LegacyIAccessible") if getattr(c, f"Get{p}Pattern")() is not None]
                print(f"  popup {w.Name[:30]!r} depth={depth} {c.ControlTypeName} {c.Name[:80]!r} patterns={pats}")


def suggestion_invoke(win: auto.Control, omni: auto.Control) -> None:
    rows = popup_rows()
    if not rows:
        raise RuntimeError("no omnibox suggestion row names the setup URL")
    row = rows[0]
    if (p := row.GetInvokePattern()) is not None:
        p.Invoke()
    else:
        row.GetLegacyIAccessiblePattern().DoDefaultAction()


def suggestion_select(win: auto.Control, omni: auto.Control) -> None:
    rows = popup_rows()
    if not rows:
        raise RuntimeError("no omnibox suggestion row names the setup URL")
    rows[0].GetSelectionItemPattern().Select()
    post_enter(win, omni)


METHODS = {
    "no-enter (control)": None,
    "PostMessage Enter to the browser window": post_enter,
    "SendMessage Enter to the browser window": send_enter,
    "UIA SetFocus + PostMessage Enter": uia_focus_then_post_enter,
    "UIA LegacyIAccessible DoDefaultAction on the omnibox": omnibox_default_action,
    "UIA Invoke / DoDefaultAction on the suggestion row": suggestion_invoke,
    "UIA Select the suggestion row + PostMessage Enter": suggestion_select,
}


def main() -> None:
    edge_exe = os.environ["EDGE"]
    subprocess.Popen([edge_exe, "--no-first-run", "--no-default-browser-check", "https://example.com"])
    time.sleep(8)
    auto.SetGlobalSearchTimeout(3)
    rows = []
    dumped = False
    for name, method in METHODS.items():
        navigated = closed = errors = 0
        nav_away = close_away = 0
        for _ in range(REPS):
            win = edge()
            before = len(tabs(win))
            front_host()
            watch = Foreground()
            try:
                win.ButtonControl(Name="New Tab").GetInvokePattern().Invoke()
                deadline = time.monotonic() + 3
                while len(tabs(win)) != before + 1 and time.monotonic() < deadline:
                    time.sleep(0.1)
                omni = omnibox(win)
                omni.GetValuePattern().SetValue(URL)
                time.sleep(0.8)
                if not dumped:
                    print("---- omnibox popup after SetValue ----")
                    dump_popup()
                    dumped = True
                if method:
                    method(win, omni)
                deadline = time.monotonic() + 3
                while not on_setup_page(win) and time.monotonic() < deadline:
                    time.sleep(0.2)
                navigated += on_setup_page(win)
            except Exception as error:
                errors += 1
                print(f"  {name}: {type(error).__name__}: {error}")
            nav_away += watch.stop() > 0
            front_host()
            watch = Foreground()
            try:
                tab = selected_tab(win)
                tab.ButtonControl(Name="Close tab").GetInvokePattern().Invoke()
                deadline = time.monotonic() + 3
                while len(tabs(win)) != before and time.monotonic() < deadline:
                    time.sleep(0.1)
                closed += len(tabs(win)) == before
            except Exception as error:
                print(f"  close after {name}: {type(error).__name__}: {error}")
            close_away += watch.stop() > 0
        rows.append(f"| {name} | {navigated}/{REPS} | {nav_away}/{REPS} | {closed}/{REPS} | {close_away}/{REPS} | {errors} |")
    lines = ["| method | navigated to the setup page | took the foreground | UIA close-tab closed it | close took the foreground | errors |", "|---|---|---|---|---|---|", *rows]
    summary = "\n".join(lines)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
        out.write(summary + "\n")


main()
