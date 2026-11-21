"""What the browser itself answers a DevTools WebSocket upgrade when its remote-debugging prompt is pressed, with no driver involved."""

import base64
import ctypes
import ctypes.wintypes as wt
import os
import socket
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import comtypes
import comtypes.client

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

BROWSER = os.environ["BROWSER"]
EXE = {"chrome": "chrome.exe", "edge": "msedge.exe"}[BROWSER]
STATE_DIR = {
    "chrome": r"%LOCALAPPDATA%\Google\Chrome\User Data",
    "edge": r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
}[BROWSER]
REPS = int(os.environ.get("REPS", "2"))
GONE = (comtypes.COMError, ValueError)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
SUMMARY: list[str] = []


def exe_of(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        return buf.value.rsplit("\\", 1)[-1].lower() if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


def browser_window() -> int:
    found = []

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if exe_of(pid.value) == EXE:
                rect = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append(((rect.right - rect.left) * (rect.bottom - rect.top), int(hwnd)))
        return True

    user32.EnumWindows(ENUM(visit), 0)
    return max(found)[1]


def new_uia():
    return comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)


def prompt_buttons(uia, hwnd: int) -> dict | None:
    root = uia.ElementFromHandle(hwnd)
    found = root.FindAll(UIA.TreeScope_Descendants, uia.CreatePropertyCondition(UIA.UIA_ClassNamePropertyId, "MdTextButton"))
    named = {}
    for index in range(found.Length):
        element = found.GetElement(index)
        rect = element.CurrentBoundingRectangle
        if rect.right > rect.left and rect.bottom > rect.top:
            named[str(element.CurrentName).strip().lower()] = element
    return named if "allow" in named and "cancel" in named else None


def look(uia, hwnd: int) -> dict | None:
    try:
        return prompt_buttons(uia, hwnd)
    except GONE:
        return None


def press(element, method: str) -> bool:
    try:
        if method == "invoke":
            element.GetCurrentPattern(UIA.UIA_InvokePatternId).QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
        else:
            rect = element.CurrentBoundingRectangle
            user32.SetCursorPos((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            time.sleep(0.03)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.03)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
        return True
    except GONE:
        return False


def endpoint() -> tuple[int, str]:
    path = os.path.join(os.path.expandvars(STATE_DIR), "DevToolsActivePort")
    for _ in range(60):
        if os.path.exists(path):
            lines = open(path, encoding="utf-8").read().split()
            if len(lines) >= 2:
                return int(lines[0]), lines[1]
        time.sleep(1)
    raise RuntimeError(f"no {path}")


def upgrade(port: int, path: str, limit_s: float) -> tuple[socket.socket, str, float]:
    started = time.monotonic()
    conn = socket.create_connection(("127.0.0.1", port), timeout=limit_s)
    key = base64.b64encode(os.urandom(16)).decode()
    conn.sendall(
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    buf = b""
    try:
        while b"\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return conn, "closed without a status line", time.monotonic() - started
            buf += chunk
    except socket.timeout:
        return conn, f"no answer within {limit_s:.0f} s", time.monotonic() - started
    return conn, buf.split(b"\r\n", 1)[0].decode(errors="replace"), time.monotonic() - started


def clear(uia, hwnd: int) -> None:
    for _ in range(3):
        row = look(uia, hwnd)
        if row is None:
            return
        press(row["cancel"], "invoke")
        time.sleep(1.5)


def double_trial(hwnd: int, port: int, path: str, first_ms: int, gap_ms: int) -> str:
    uia = new_uia()
    clear(uia, hwnd)
    result: dict = {}

    def person() -> None:
        comtypes.CoInitializeEx(0)
        puia = new_uia()
        start = time.monotonic()
        while time.monotonic() - start < 15:
            row = look(puia, hwnd)
            if row:
                time.sleep(first_ms / 1000)
                press(row["cancel"], "invoke")
                time.sleep(gap_ms / 1000)
                first_left = look(puia, hwnd) is not None
                result["first_left"] = first_left
                result["second"] = press(row["allow"], "invoke") if first_left else None
                return
            time.sleep(0.01)

    watcher = threading.Thread(target=person, daemon=True)
    watcher.start()
    conn, status, took = upgrade(port, path, 20)
    watcher.join(5)
    conn.close()
    first = "dropped" if result.get("first_left") else "landed"
    line = f"| Cancel at {first_ms} ms ({first}) | Allow {gap_ms} ms later | {status} | {took:.1f} s |"
    clear(uia, hwnd)
    time.sleep(1)
    return line


def trial(hwnd: int, port: int, path: str, button: str, delay_ms: int, method: str) -> str:
    uia = new_uia()
    clear(uia, hwnd)
    result: dict = {}

    def person() -> None:
        comtypes.CoInitializeEx(0)
        puia = new_uia()
        start = time.monotonic()
        while time.monotonic() - start < 15:
            row = look(puia, hwnd)
            if row:
                seen = time.monotonic()
                result["seen_ms"] = round((seen - start) * 1000)
                time.sleep(delay_ms / 1000)
                result["pressed"] = press(row[button], method)
                after = time.monotonic()
                gone = None
                while time.monotonic() - after < 3:
                    if look(puia, hwnd) is None:
                        gone = round((time.monotonic() - after) * 1000)
                        break
                    time.sleep(0.02)
                result["gone_ms"] = gone
                return
            time.sleep(0.01)

    watcher = threading.Thread(target=person, daemon=True)
    watcher.start()
    conn, status, took = upgrade(port, path, 20)
    watcher.join(5)
    conn.close()
    gone = result.get("gone_ms")
    left = "closed" if gone is not None else "still open after 3 s"
    line = f"| {button} | {method} | {delay_ms} ms | {result.get('seen_ms')} ms | {left} | {status} | {took:.1f} s |"
    clear(uia, hwnd)
    time.sleep(1)
    return line


def emit(line: str) -> None:
    print(line, flush=True)
    SUMMARY.append(line)


def main() -> None:
    hwnd = browser_window()
    port, path = endpoint()
    if os.environ.get("PROBE") == "second":
        emit(f"### {BROWSER}: single Invoke threshold\n")
        emit("| button | press | after prompt shown | prompt seen | prompt | upgrade answer | upgrade took |")
        emit("|---|---|---|---|---|---|---|")
        for delay in (450, 500, 550, 600, 700):
            for _ in range(REPS):
                emit(trial(hwnd, port, path, "cancel", delay, "invoke"))
        emit(f"\n### {BROWSER}: second press after a dropped first press\n")
        emit("| first | second | upgrade answer | upgrade took |")
        emit("|---|---|---|---|")
        for first in (0, 200, 400):
            for gap in (300, 500, 700, 1000):
                for _ in range(REPS):
                    emit(double_trial(hwnd, port, path, first, gap))
        with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
            out.write("\n".join(SUMMARY) + "\n")
        return
    emit(f"### {BROWSER}: prompt pressed by a person, browser's answer to the upgrade\n")
    emit("| button | press | after prompt shown | prompt seen | prompt | upgrade answer | upgrade took |")
    emit("|---|---|---|---|---|---|---|")
    for button in ("cancel", "allow"):
        for method in ("invoke", "click"):
            for delay in (0, 200, 400, 800, 1500):
                for _ in range(REPS):
                    emit(trial(hwnd, port, path, button, delay, method))
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
        out.write("\n".join(SUMMARY) + "\n")


if __name__ == "__main__":
    main()
