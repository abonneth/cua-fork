"""What an existing-profile attach returns when a person cancels Edge's remote-debugging prompt N ms after it appears."""

import json
import os
import re
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
DELAYS = [None] + [int(value) for value in os.environ.get("DELAYS_MS", "0,250,450,550,650,800,1000,1300,1700,2500").split(",")]
BUTTON_CLASS = "MdTextButton"


def structured(reply: dict) -> dict:
    return (reply.get("result") or {}).get("structuredContent") or {}


def edge_window(daemon: Daemon) -> dict:
    windows = structured(daemon.call("list_windows", {"on_screen_only": True})).get("windows", [])
    edges = [w for w in windows if "edge" in str(w.get("app_name", "")).lower() and w.get("title")]
    best = max(edges, key=lambda w: (w.get("bounds") or {}).get("width", 0) * (w.get("bounds") or {}).get("height", 0))
    return {"pid": best["pid"], "window_id": best["window_id"]}


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


class Person:
    """Watches the window for the prompt and presses Cancel `delay_ms` after first seeing it; None only watches."""

    def __init__(self, hwnd: int, delay_ms: int | None) -> None:
        self.hwnd, self.delay_ms = hwnd, delay_ms
        self.start = time.monotonic()
        self.seen_at: float | None = None
        self.cancel_at: float | None = None
        self.cancel = "not attempted"
        self.names = ""
        self.poll_ms: list[float] = []
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def ms(self) -> float:
        return (time.monotonic() - self.start) * 1000

    def run(self) -> None:
        comtypes.CoInitializeEx(0)
        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        row = None
        while self.running and row is None:
            before = self.ms()
            try:
                row = consent_row(uia, self.hwnd)
            except comtypes.COMError:
                row = None
            self.poll_ms.append(self.ms() - before)
            if row is None:
                time.sleep(0.02)
        if row is None:
            return
        self.seen_at = self.ms()
        try:
            self.names = " | ".join(button.CurrentName for button in row)
        except comtypes.COMError:
            self.names = "(gone before its names were read)"
        if self.delay_ms is None:
            return
        wait = self.seen_at + self.delay_ms - self.ms()
        if wait > 0:
            time.sleep(wait / 1000)
        self.cancel_at = self.ms()
        try:
            pattern = row[2].GetCurrentPattern(UIA.UIA_InvokePatternId).QueryInterface(UIA.IUIAutomationInvokePattern)
            pattern.Invoke()
            self.cancel = "pressed"
        except comtypes.COMError as error:
            self.cancel = f"prompt gone ({error.hresult & 0xFFFFFFFF:#x})"
        except ValueError:
            self.cancel = "prompt gone"

    def stop(self) -> None:
        self.running = False
        self.thread.join(10)


def refusal(reply: dict) -> tuple[str, str]:
    text = json.dumps(reply)
    code = re.search(r'"(?:refusal_code|code)":\s*"([a-z_]+)"', text)
    message = re.search(r'"message":\s*"((?:[^"\\]|\\.)*)"', text)
    cause = re.search(r'claim_error\W+((?:[^"\\]|\\.)*)', text)
    return (code.group(1) if code else "-", (message.group(1) if message else text)[:230] + (f" [claim error: {cause.group(1)[:400]}]" if cause else ""))


def leftover_prompt(hwnd: int) -> bool:
    uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    try:
        row = consent_row(uia, hwnd)
    except comtypes.COMError:
        return False
    if row is None:
        return False
    try:
        row[2].GetCurrentPattern(UIA.UIA_InvokePatternId).QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
    except (comtypes.COMError, ValueError):
        pass
    return True


def one(delay_ms: int | None, rep: int) -> dict:
    name = f"{LABEL}-{'watch' if delay_ms is None else delay_ms}-{rep}"
    daemon = Daemon(EXE, rf"\\.\pipe\ab-consent-{name}", ("--grant", "existing-profile"), CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    try:
        where = {"session": name, **edge_window(daemon)}
        daemon.call("start_session", {"session": name})
        person = Person(where["window_id"], delay_ms)
        started = time.monotonic()
        prepared = daemon.call("browser_prepare", {**where, "strategy": {"kind": "existing_profile"}})
        elapsed = (time.monotonic() - started) * 1000
        person.stop()
        bound = bool(structured(daemon.call("get_browser_state", where)).get("target_id"))
        code, message = ("-", "bound") if bound else refusal(prepared)
        if not bound and rep == 1:
            print("RAW", json.dumps(prepared)[:3000], flush=True)
        left = leftover_prompt(where["window_id"])
        daemon.call("end_session", {"session": name})
    finally:
        daemon.stop()
    time.sleep(2)
    polls = sorted(person.poll_ms)
    row = {
        "delay": delay_ms,
        "seen": None if person.seen_at is None else round(person.seen_at),
        "cancel_at": None if person.cancel_at is None else round(person.cancel_at),
        "cancel": person.cancel,
        "prepare_ms": round(elapsed),
        "bound": bound,
        "code": code,
        "message": message,
        "left": left,
        "poll_p50": round(polls[len(polls) // 2]) if polls else None,
        "names": person.names,
    }
    print(json.dumps(row), flush=True)
    return row


def main() -> None:
    rows = [one(delay, rep) for delay in DELAYS for rep in range(1, REPS + 1)]
    lines = [
        f"### {LABEL}",
        "",
        "| person cancels after | prompt seen at | cancel | attach result | attach ms | prompt left on screen |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        result = "bound" if row["bound"] else f"`{row['code']}`: {row['message']}"
        delay = "never (watch only)" if row["delay"] is None else f"{row['delay']} ms"
        lines.append(f"| {delay} | {row['seen']} ms | {row['cancel']} at {row['cancel_at']} ms | {result} | {row['prepare_ms']} | {row['left']} |")
    names = next((row["names"] for row in rows if row["names"]), "")
    lines += ["", f"prompt buttons (extra | allow | cancel): {names}"]
    summary = "\n".join(lines)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
        out.write(summary + "\n")


main()
