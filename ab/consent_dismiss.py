"""What an existing-profile attach returns when a person answers Edge's remote-debugging prompt before the driver does."""

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import comtypes
import comtypes.client

from driver import CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, Daemon

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

EXE, LABEL = sys.argv[1], sys.argv[2]
REPS = int(os.environ.get("REPS", "3"))
BUTTON_CLASS = "MdTextButton"
GONE = (comtypes.COMError, ValueError)
SUMMARY: list[str] = []


def structured(reply: dict) -> dict:
    return (reply.get("result") or {}).get("structuredContent") or {}


def outcome(reply: dict) -> str:
    body = structured(reply)
    refusal = body.get("refusal")
    if refusal:
        return f"`{refusal.get('code')}`: {refusal.get('message')}"
    if body.get("target_id"):
        return "bound"
    return "attached" if body.get("action") else f"status={body.get('status')}"


def edge_window(daemon: Daemon) -> dict:
    windows = structured(daemon.call("list_windows", {"on_screen_only": True})).get("windows", [])
    edges = [w for w in windows if "edge" in str(w.get("app_name", "")).lower() and w.get("title")]
    best = max(edges, key=lambda w: (w.get("bounds") or {}).get("width", 0) * (w.get("bounds") or {}).get("height", 0))
    return {"pid": best["pid"], "window_id": best["window_id"]}


def new_uia():
    return comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)


def consent_row(uia, hwnd: int):
    """The prompt's (extra, allow, cancel) buttons: three dialog buttons on one row, the standard pair adjacent."""
    root = uia.ElementFromHandle(hwnd)
    found = root.FindAll(UIA.TreeScope_Descendants, uia.CreatePropertyCondition(UIA.UIA_ClassNamePropertyId, BUTTON_CLASS))
    rows: dict[tuple[int, int], list] = {}
    for index in range(found.Length):
        element = found.GetElement(index)
        rect = element.CurrentBoundingRectangle
        if rect.right > rect.left and rect.bottom > rect.top:
            rows.setdefault((rect.top, rect.bottom), []).append((rect.left, rect.right, element))
    for buttons in rows.values():
        if len(buttons) != 3:
            continue
        first, second, third = sorted(buttons, key=lambda button: button[0])
        if second[0] - first[1] > third[0] - second[1]:
            return first[2], second[2], third[2]
        return third[2], second[2], first[2]
    return None


def look(uia, hwnd: int):
    try:
        return consent_row(uia, hwnd)
    except GONE:
        return None


def press(button) -> bool:
    try:
        button.GetCurrentPattern(UIA.UIA_InvokePatternId).QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
        return True
    except GONE:
        return False


class Person:
    """Presses `button` on the prompt `delay_ms` after seeing it, on at most `presses` prompts; with `every` it keeps watching."""

    def __init__(self, hwnd: int, button: str | None, delay_ms: int = 0, every: bool = False, presses: int = 99) -> None:
        self.hwnd, self.button, self.delay_ms, self.every, self.presses = hwnd, button, delay_ms, every, presses
        self.start = time.monotonic()
        self.seen: list[int] = []
        self.pressed: list[int] = []
        self.missed = 0
        self.names = ""
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def ms(self) -> float:
        return (time.monotonic() - self.start) * 1000

    def wait_for(self, uia, present: bool, limit_ms: float = float("inf")):
        until = self.ms() + limit_ms
        while self.running and self.ms() < until:
            row = look(uia, self.hwnd)
            if (row is not None) == present:
                return row
            time.sleep(0.02)
        return None

    def run(self) -> None:
        comtypes.CoInitializeEx(0)
        uia = new_uia()
        while self.running:
            row = self.wait_for(uia, True)
            if row is None:
                return
            seen = self.ms()
            self.seen.append(round(seen))
            try:
                self.names = self.names or " | ".join(button.CurrentName for button in row)
            except GONE:
                pass
            if self.button is not None and len(self.pressed) + self.missed < self.presses:
                wait = seen + self.delay_ms - self.ms()
                if wait > 0:
                    time.sleep(wait / 1000)
                if press(row[2] if self.button == "cancel" else row[1]):
                    self.pressed.append(round(self.ms()))
                else:
                    self.missed += 1
            if not self.every:
                return
            self.wait_for(uia, False, 5000)

    def stop(self) -> None:
        self.running = False
        self.thread.join(10)

    def report(self) -> str:
        acted = f"pressed {self.button} at {self.pressed} ms" if self.button else "watched"
        return f"prompt seen at {self.seen} ms, {acted}" + (f", prompt already gone {self.missed}x" if self.missed else "")


def clear_prompt(hwnd: int) -> bool:
    row = look(new_uia(), hwnd)
    if row is None:
        return False
    press(row[2])
    time.sleep(1)
    return True


class TcpRow(ctypes.Structure):
    _fields_ = [(name, wt.DWORD) for name in ("state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]


class TcpEntry(ctypes.Structure):
    _fields_ = [(name, wt.DWORD) for name in ("state", "local_addr", "local_port", "remote_addr", "remote_port")]


def drop_sockets(pid: int) -> str:
    """Deletes the IPv4 TCP connections `pid` holds, as a browser that closes its DevTools socket would."""
    iphlpapi = ctypes.WinDLL("iphlpapi")
    size = wt.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 4, 0)
    table = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedTcpTable(table, ctypes.byref(size), False, 2, 4, 0):
        return "could not list connections"
    count = wt.DWORD.from_buffer(table).value
    rows = (TcpRow * count).from_buffer(table, 4)
    results = []
    for row in rows:
        if row.pid == pid:
            entry = TcpEntry(12, row.local_addr, row.local_port, row.remote_addr, row.remote_port)
            results.append(iphlpapi.SetTcpEntry(ctypes.byref(entry)))
    return f"{len(results)} connection(s), SetTcpEntry={results}"


class Run:
    def __init__(self, name: str) -> None:
        self.name = f"{LABEL}-{name}"
        self.daemon = Daemon(EXE, rf"\\.\pipe\ab-consent-{self.name}", ("--grant", "existing-profile"), CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        self.where = {"session": self.name, **edge_window(self.daemon)}
        self.hwnd = self.where["window_id"]
        self.daemon.call("start_session", {"session": self.name})

    def timed(self, tool: str, args: dict, person: Person | None = None) -> tuple[dict, int]:
        started = time.monotonic()
        reply = self.daemon.call(tool, args)
        elapsed = round((time.monotonic() - started) * 1000)
        if person:
            person.stop()
        return reply, elapsed

    def prepare(self, person: Person | None = None) -> tuple[dict, int]:
        return self.timed("browser_prepare", {**self.where, "strategy": {"kind": "existing_profile"}}, person)

    def state(self, person: Person | None = None) -> tuple[dict, int]:
        return self.timed("get_browser_state", self.where, person)

    def close(self) -> bool:
        left = clear_prompt(self.hwnd)
        self.daemon.call("end_session", {"session": self.name})
        self.daemon.stop()
        time.sleep(2)
        return left


def emit(line: str) -> None:
    print(line, flush=True)
    SUMMARY.append(line)


def answered_first(button: str, delays: list[int]) -> None:
    emit(f"\n#### A person presses {button.capitalize()} before the driver acts\n")
    emit("| pressed after | person | `browser_prepare` | ms | `get_browser_state` after | prompt left |")
    emit("|---|---|---|---|---|---|")
    for delay in delays:
        for rep in range(1, REPS + 1):
            run = Run(f"{button}-{delay}-{rep}")
            person = Person(run.hwnd, button, delay)
            reply, elapsed = run.prepare(person)
            state, _ = run.state()
            if rep == 1:
                print("RAW prepare", json.dumps(structured(reply))[:1500], flush=True)
                print("RAW state", json.dumps(structured(state))[:600], flush=True)
            emit(f"| {delay} ms | {person.report()} | {outcome(reply)} | {elapsed} | {outcome(state)} | {run.close()} |")


def retry_after_cancel() -> None:
    emit("\n#### The caller follows the refusal's hint after a Cancel, nobody touches the second prompt\n")
    emit("| first `browser_prepare` (person cancels at 200 ms) | hint | `get_browser_state` | second `browser_prepare` | second prompt | final `get_browser_state` |")
    emit("|---|---|---|---|---|---|")
    for rep in range(1, REPS + 1):
        run = Run(f"retry-{rep}")
        first, _ = run.prepare(Person(run.hwnd, "cancel", 200))
        hint = structured(first).get("hint")
        state, _ = run.state()
        watcher = Person(run.hwnd, None)
        second, elapsed = run.prepare(watcher)
        final, _ = run.state()
        emit(f"| {outcome(first)} | {hint} | {outcome(state)} | {outcome(second)} in {elapsed} ms | {watcher.report()} | {outcome(final)} |")
        run.close()


def reconnect(button: str | None, presses: int = 99) -> None:
    acts = {None: "watches", 1: "cancels the first prompt only"}.get(button and presses, "cancels every prompt")
    for rep in range(1, REPS + 1):
        run = Run(f"reconnect-{button}-{presses}-{rep}")
        bound, _ = run.prepare()
        before, _ = run.state()
        dropped = drop_sockets(run.daemon.process.pid)
        time.sleep(1)
        person = Person(run.hwnd, button, 0, every=True, presses=presses)
        after, elapsed = run.state(person)
        again, _ = run.state()
        emit(f"| {outcome(bound)} / {outcome(before)} | {dropped} | {acts}: {person.report()} | {outcome(after)} | {elapsed} | {outcome(again)} | {run.close()} |")
        print("RAW", json.dumps(structured(after))[:1500], flush=True)


def main() -> None:
    emit(f"### {LABEL}")
    answered_first("cancel", [0, 200, 400])
    retry_after_cancel()
    answered_first("allow", [0, 200])
    emit("\n#### The bound socket is dropped, then `get_browser_state`\n")
    emit("| attach / state before | sockets dropped | person | `get_browser_state` after | ms | `get_browser_state` again | prompt left |")
    emit("|---|---|---|---|---|---|---|")
    reconnect(None)
    reconnect("cancel", 1)
    reconnect("cancel")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
        out.write("\n".join(SUMMARY) + "\n")


main()
