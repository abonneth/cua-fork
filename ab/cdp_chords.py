"""Do browser shortcuts sent to a page over CDP `Input.dispatchKeyEvent` reach the browser frame (tabs, reload)?"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import threading
import time
import urllib.request

import websocket

BASE = f"http://127.0.0.1:{os.environ.get('CDP_PORT', '9222')}"
CHORDS = {
    "ctrl+t (new tab)": ("t", "KeyT", 84, 2),
    "ctrl+w (close tab)": ("w", "KeyW", 87, 2),
    "ctrl+l (address bar)": ("l", "KeyL", 76, 2),
    "f5 (reload)": ("F5", "F5", 116, 0),
    "ctrl+r (reload)": ("r", "KeyR", 82, 2),
}


user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
kernel32.OpenProcess.restype = wt.HANDLE


def foreground() -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    buf, size = ctypes.create_unicode_buffer(512), wt.DWORD(512)
    kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(handle)
    return buf.value.rsplit("\\", 1)[-1] or "?"


def pages() -> list[dict]:
    return [t for t in json.load(urllib.request.urlopen(f"{BASE}/json/list")) if t["type"] == "page"]


class Page:
    def __init__(self, target: dict) -> None:
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10)
        self.next = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next += 1
        self.ws.send(json.dumps({"id": self.next, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self.next:
                return reply

    def value(self, expression: str):
        return self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})["result"]["result"].get("value")

    def chord(self, key: str, code: str, vk: int, modifiers: int) -> None:
        if modifiers:
            self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "Control", "code": "ControlLeft", "windowsVirtualKeyCode": 17, "modifiers": 2})
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": modifiers}
        self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})
        if modifiers:
            self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Control", "code": "ControlLeft", "windowsVirtualKeyCode": 17})


def fresh_page() -> Page:
    request = urllib.request.Request(f"{BASE}/json/new?about:blank", method="PUT")
    page = Page(json.load(urllib.request.urlopen(request)))
    page.call("Page.navigate", {"url": "data:text/html,<input id=f value=hello><script>window.loadedAt=performance.timeOrigin;document.addEventListener('keydown',e=>{(window.keys=window.keys||[]).push((e.ctrlKey?'ctrl+':'')+e.key)})</script>"})
    time.sleep(1.5)
    page.value("document.getElementById('f').focus()")
    return page


def front_away_from_edge() -> str:
    hwnd = HOST
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(0.3)
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, fg_thread, True)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.AttachThreadInput(me, fg_thread, False)
    time.sleep(0.5)
    return foreground()


def host_window() -> int:
    ready = threading.Event()
    box = {}

    def pump() -> None:
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        box["hwnd"] = user32.CreateWindowExW(0, "Edit", "host app", 0x10CF0000, 100, 100, 600, 400, None, None, None, None)
        ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=pump, daemon=True).start()
    ready.wait()
    return box["hwnd"]


HOST = host_window()
time.sleep(1)
print("host hwnd:", HOST, "foreground after reset:", front_away_from_edge())
print("foreground before the chords:", foreground())
lines = ["| chord sent to the page over CDP | page saw keydown | browser effect | foreground before -> after |", "|---|---|---|---|"]
for name, chord in CHORDS.items():
    page = fresh_page()
    tabs_before, loaded_before = len(pages()), page.value("window.loadedAt")
    front_before = foreground()
    try:
        page.chord(*chord)
    except websocket.WebSocketConnectionClosedException:
        pass
    time.sleep(2)
    tabs_after, front_after = len(pages()), foreground()
    try:
        seen, loaded_after = page.value("window.keys||[]"), page.value("window.loadedAt")
    except Exception:
        seen, loaded_after = "(tab closed)", loaded_before
    effect = []
    if tabs_after != tabs_before:
        effect.append(f"tabs {tabs_before} -> {tabs_after}")
    if loaded_after != loaded_before:
        effect.append("reloaded")
    lines.append(f"| {name} | {seen} | {', '.join(effect) or 'none'} | {front_before} -> {front_after} |")
    print(lines[-1])
    page.ws.close()

page = fresh_page()
page.chord("a", "KeyA", 65, 2)
time.sleep(0.5)
selected = page.value("(e=>e.selectionEnd-e.selectionStart)(document.getElementById('f'))")
lines.append(f"| control: ctrl+a in a text field | {page.value('window.keys||[]')} | selected {selected} of 5 chars |")
print(lines[-1])


def browser_call(method: str, params: dict) -> dict:
    ws = websocket.create_connection(json.load(urllib.request.urlopen(f"{BASE}/json/version"))["webSocketDebuggerUrl"], timeout=10)
    ws.send(json.dumps({"id": 1, "method": method, "params": params}))
    reply = json.loads(ws.recv())
    ws.close()
    return reply


def ctrl_t() -> None:
    page = fresh_page()
    front_away_from_edge()
    page.chord("t", "KeyT", 84, 2)
    page.ws.close()


OPENERS = {
    "ctrl+t over CDP Input.dispatchKeyEvent": ctrl_t,
    "Target.createTarget background=true": lambda: browser_call("Target.createTarget", {"url": "about:blank", "background": True}),
    "Target.createTarget background=false": lambda: browser_call("Target.createTarget", {"url": "about:blank", "background": False}),
    "control: relaunch msedge.exe with a URL": lambda: subprocess.Popen([os.environ["EDGE"], f"--user-data-dir={os.environ['RUNNER_TEMP']}\\edge", "about:blank"]),
}
REPS = 5
lines += ["", "| opening a tab, foreground on another app | tab opened | Edge took the foreground | foreground before |", "|---|---|---|---|"]
for name, opener in OPENERS.items():
    opened = fronted = 0
    befores = set()
    for _ in range(REPS):
        if name.startswith("ctrl+t"):
            tabs_before = len(pages()) + 1
        else:
            tabs_before = len(pages())
        before = front_away_from_edge()
        befores.add(before)
        opener()
        time.sleep(2)
        opened += len(pages()) > tabs_before
        fronted += foreground() == "msedge.exe"
    lines.append(f"| {name} | {opened}/{REPS} | {fronted}/{REPS} | {', '.join(sorted(befores))} |")
    print(lines[-1])

summary = "\n".join(lines)
with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
    out.write(summary + "\n")
