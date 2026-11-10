"""Which steps of a cua-driver existing-profile attach take the foreground from the user's app, per session."""

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time

from driver import CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, Daemon, exe_name, user32

kernel32 = ctypes.WinDLL("kernel32")
REPS = int(os.environ.get("REPS", "5"))
SETTLE_S = 1.5
EXE, VARIANT = sys.argv[1], sys.argv[2]


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
    """Every foreground change while it runs, polled every 2 ms: (seconds, owner exe, window class)."""

    def __init__(self) -> None:
        self.changes: list[tuple[float, int, str]] = []
        self.start = time.monotonic()
        self.running = True
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.thread.start()

    def poll(self) -> None:
        last = None
        while self.running:
            hwnd = user32.GetForegroundWindow() or 0
            if hwnd != last:
                self.changes.append((time.monotonic() - self.start, hwnd, describe(hwnd)))
                last = hwnd
            time.sleep(0.002)

    def stop(self) -> list[tuple[str, float]]:
        """Each stay away from the host: who held the foreground and for how many ms."""
        time.sleep(SETTLE_S)
        self.running = False
        self.thread.join()
        end = time.monotonic() - self.start
        away = []
        for (t, hwnd, who), nxt in zip(self.changes, self.changes[1:] + [(end, 0, "")]):
            if hwnd != HOST:
                away.append((who, round((nxt[0] - t) * 1000)))
        return away


def describe(hwnd: int) -> str:
    if not hwnd:
        return "(none)"
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, cls, 128)
    return f"{exe_name(pid.value)}:{cls.value}"


def structured(reply: dict) -> dict:
    result = reply.get("result") or {}
    return result.get("structuredContent") or {}


def edge_window(daemon: Daemon) -> dict:
    windows = structured(daemon.call("list_windows", {"on_screen_only": True})).get("windows", [])
    edges = [w for w in windows if "edge" in str(w.get("app_name", "")).lower() and w.get("title")]
    best = max(edges, key=lambda w: (w.get("bounds") or {}).get("width", 0) * (w.get("bounds") or {}).get("height", 0))
    return {"pid": best["pid"], "window_id": best["window_id"]}


def attach(daemon: Daemon, where: dict) -> tuple[str, dict]:
    state = daemon.call("get_browser_state", where)
    if structured(state).get("target_id"):
        return "already bound", {}
    prepared = daemon.call("browser_prepare", {**where, "strategy": {"kind": "existing_profile"}})
    effects = structured(prepared).get("side_effects") or (structured(prepared).get("detail") or {}).get("setup_side_effects") or {}
    state = daemon.call("get_browser_state", where)
    if not structured(state).get("target_id"):
        return "FAILED " + json.dumps(prepared)[:1500], effects
    return "bound", effects


def shot(name: str) -> None:
    from PIL import ImageGrab

    os.makedirs("shots", exist_ok=True)
    ImageGrab.grab(all_screens=True).save(os.path.join("shots", name))


def main() -> None:
    daemon = Daemon(EXE, rf"\\.\pipe\ab-focus-{VARIANT}", ("--grant", "existing-profile"), CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    rows = []
    try:
        for rep in range(1, REPS + 1):
            session = f"s{rep}"
            where = {"session": session, **edge_window(daemon)}
            daemon.call("start_session", {"session": session})
            phases = {}
            fronted = front_host()
            watch = Foreground()
            outcome, effects = attach(daemon, where)
            phases["attach"] = watch.stop()
            if outcome.startswith("FAILED") and rep == 1:
                shot(f"failed-attach-{VARIANT}.png")
            state = structured(daemon.call("get_browser_state", where))
            tab = next((t["tab_id"] for t in state.get("tabs", []) if t.get("active")), None)
            front_host()
            watch = Foreground()
            if tab:
                daemon.call("browser_navigate", {**where, "target_id": state["target_id"], "tab_id": tab, "url": f"https://example.com/?rep={rep}"})
            phases["act"] = watch.stop()
            front_host()
            watch = Foreground()
            ended = daemon.call("end_session", {"session": session})
            phases["end_session"] = watch.stop()
            done = sorted(k for k, v in effects.items() if v is True)
            print(f"rep {rep}: host fronted={fronted} attach={outcome} side_effects={done} end={json.dumps(structured(ended))[:160]}")
            for phase, away in phases.items():
                print(f"  {phase}: away={away}")
            rows.append((phases, done))
    finally:
        daemon.stop()

    lines = [f"### {VARIANT}", "", "| phase | reps that took the foreground | total ms away | who |", "|---|---|---|---|"]
    for phase in ("attach", "act", "end_session"):
        aways = [phases[phase] for phases, _ in rows]
        hit = sum(1 for a in aways if a)
        total = sum(ms for a in aways for _, ms in a)
        who = sorted({w for a in aways for w, _ in a})
        lines.append(f"| {phase} | {hit}/{len(rows)} | {total} | {', '.join(who) or '-'} |")
    effects = sorted({e for _, done in rows for e in done})
    lines += ["", f"attach side effects reported by the driver: {', '.join(effects) or 'none'}"]
    summary = "\n".join(lines)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
        out.write(summary + "\n")


main()
