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


def owner(hwnd: int | None) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, cls, 128)
    return f"{pid.value}:{cls.value}"


def pattern(control: auto.Control, name: str):
    try:
        return control.GetPattern(getattr(auto.PatternId, f"{name}Pattern"))
    except Exception:
        return None


class Foreground:
    """Total ms the foreground spent away from the host while it ran, polled every 2 ms."""

    def __init__(self) -> None:
        self.away_ms = 0.0
        self.who: set[str] = set()
        self.running = True
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.thread.start()

    def poll(self) -> None:
        last = time.monotonic()
        while self.running:
            now = time.monotonic()
            fg = user32.GetForegroundWindow()
            if fg != HOST:
                self.away_ms += (now - last) * 1000
                self.who.add(owner(fg))
            last = now
            time.sleep(0.002)

    def stop(self) -> int:
        time.sleep(1.0)
        self.running = False
        self.thread.join()
        if self.away_ms:
            print(f"    away {round(self.away_ms)} ms: {sorted(self.who)} (host {HOST}, edge pids {edge_pids()})")
        return round(self.away_ms)


def edge_pids() -> list[int]:
    out = subprocess.run(["tasklist", "/fi", "imagename eq msedge.exe", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
    return sorted({int(line.split('","')[1]) for line in out.splitlines() if line.count('","') >= 1})[:3]


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
                pats = [p for p in ("Invoke", "SelectionItem", "LegacyIAccessible") if pattern(c, p) is not None]
                print(f"  popup {w.Name[:30]!r} depth={depth} {c.ControlTypeName} {c.Name[:80]!r} patterns={pats}")


def suggestion_invoke(win: auto.Control, omni: auto.Control) -> None:
    rows = popup_rows()
    if not rows:
        raise RuntimeError("no omnibox suggestion row names the setup URL")
    row = rows[0]
    if (p := pattern(row, "Invoke")) is not None:
        p.Invoke()
    else:
        pattern(row, "LegacyIAccessible").DoDefaultAction()


def suggestion_select(win: auto.Control, omni: auto.Control) -> None:
    rows = popup_rows()
    if not rows:
        raise RuntimeError("no omnibox suggestion row names the setup URL")
    rows[0].GetSelectionItemPattern().Select()
    post_enter(win, omni)


def checkbox(win: auto.Control) -> auto.Control | None:
    for c, _ in auto.WalkControl(win, maxDepth=16):
        if c.ControlTypeName == "CheckBoxControl" and pattern(c, "Toggle") is not None:
            return c
    return None


def wait(cond, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.1)
    return cond()


def step(results: dict, name: str, action) -> bool:
    front_host()
    watch = Foreground()
    try:
        ok = bool(action())
    except Exception as error:
        print(f"  {name}: {type(error).__name__}: {error}")
        ok = False
    away = watch.stop()
    done, stole = results.setdefault(name, [0, 0])
    results[name] = [done + ok, stole + (away > 0)]
    return ok


def main() -> None:
    edge_exe = os.environ["EDGE"]
    subprocess.Popen([edge_exe, "--no-first-run", "--no-default-browser-check", "https://example.com"])
    time.sleep(8)
    auto.SetGlobalSearchTimeout(3)
    results: dict = {}
    for rep in range(REPS):
        win = edge()
        before = len(tabs(win))
        print(f"rep {rep + 1}")

        def new_tab() -> bool:
            pattern(win.ButtonControl(Name="New Tab"), "Invoke").Invoke()
            return wait(lambda: len(tabs(win)) == before + 1)

        def set_url() -> bool:
            pattern(omnibox(win), "Value").SetValue(URL)
            return True

        def enter() -> bool:
            post_enter(win, None)
            return wait(lambda: on_setup_page(win))

        def toggle_on() -> bool:
            box = wait(lambda: checkbox(win) is not None, 5) and checkbox(win)
            pattern(box, "Toggle").Toggle()
            return pattern(box, "Toggle").ToggleState == 1

        def toggle_off() -> bool:
            box = checkbox(win)
            pattern(box, "Toggle").Toggle()
            return pattern(box, "Toggle").ToggleState == 0

        def close_invoke() -> bool:
            pattern(selected_tab(win).ButtonControl(Name="Close tab"), "Invoke").Invoke()
            return wait(lambda: len(tabs(win)) == before)

        step(results, "1 new tab: UIA Invoke New Tab button", new_tab)
        step(results, "2 omnibox: UIA SetValue", set_url)
        step(results, "3 navigate: PostMessage Enter", enter)
        step(results, "4 checkbox on: UIA Toggle", toggle_on)
        step(results, "5 checkbox off: UIA Toggle", toggle_off)
        step(results, "6 close tab: UIA Invoke Close tab button", close_invoke)
    lines = ["| step | worked | took the foreground |", "|---|---|---|"]
    lines += [f"| {name} | {done}/{REPS} | {stole}/{REPS} |" for name, (done, stole) in results.items()]
    summary = "\n".join(lines)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
        out.write(summary + "\n")


main()
