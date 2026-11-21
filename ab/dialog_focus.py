"""Foreground hotkey that opens a dialog in the app already in front: who holds the focus afterwards."""

import json
import os
import socket
import subprocess
import sys
import time

SOCK = "/tmp/cua-repro.sock"
ENV = {**os.environ, "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}
WATCH_S = 3.0
REPS = int(os.environ.get("REPS", "5"))


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, env=ENV).stdout.strip()


def request(payload: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(SOCK)
        conn.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b"{}")


def call(tool: str, args: dict) -> dict:
    return request({"method": "call", "name": tool, "args": {"session": "repro", **args}, "client_kind": "cli"})


def active() -> int:
    out = sh("xprop", "-root", "_NET_ACTIVE_WINDOW")
    return int(out.rsplit(" ", 1)[-1], 16) if "0x" in out else 0


def core_focus() -> int:
    out = sh("xdotool", "getwindowfocus", "-f")
    return int(out) if out.isdigit() else 0


def name(window: int) -> str:
    return sh("xdotool", "getwindowname", str(window)) if window else "-"


def app_windows(pid: int) -> list[int]:
    return [int(w) for w in sh("xdotool", "search", "--onlyvisible", "--pid", str(pid)).split()]


def start_mousepad() -> tuple[int, int]:
    subprocess.run(["pkill", "-x", "mousepad"], env=ENV)
    time.sleep(1)
    with open("/tmp/note.txt", "w") as handle:
        handle.write("hello\n")
    proc = subprocess.Popen(["mousepad", "/tmp/note.txt"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    window = int(sh("xdotool", "search", "--sync", "--onlyvisible", "--pid", str(proc.pid)).split()[0])
    sh("xdotool", "windowactivate", "--sync", str(window))
    time.sleep(1)
    return proc.pid, window


def watch(pid: int, main: int) -> dict:
    dialog_seen_ms = None
    started = time.monotonic()
    while time.monotonic() - started < WATCH_S:
        others = [w for w in app_windows(pid) if w != main]
        if others and dialog_seen_ms is None:
            dialog_seen_ms = round((time.monotonic() - started) * 1000)
        time.sleep(0.05)
    others = [w for w in app_windows(pid) if w != main]
    dialog = others[-1] if others else 0
    act, core = active(), core_focus()
    return {
        "dialog": name(dialog),
        "dialog_seen_ms": dialog_seen_ms,
        "active": "dialog" if act == dialog and dialog else ("main" if act == main else name(act)),
        "core_focus": "dialog" if core == dialog and dialog else ("main" if core == main else name(core)),
    }


def close_dialogs(pid: int, main: int) -> None:
    for window in app_windows(pid):
        if window != main:
            sh("xdotool", "windowactivate", "--sync", str(window))
            sh("xdotool", "key", "Escape")
    time.sleep(0.5)


def main_() -> None:
    driver = sys.argv[1]
    print(sh(driver, "--version"))
    daemon = subprocess.Popen([driver, "serve", "--socket", SOCK], env=ENV, stdout=subprocess.DEVNULL, stderr=open("/tmp/serve.log", "w"))
    for _ in range(100):
        if os.path.exists(SOCK):
            break
        time.sleep(0.2)
    print("start_session", json.dumps(call("start_session", {}))[:300])
    rows = []
    for arm in ("driver hotkey, foreground", "xdotool key (control)"):
        for rep in range(REPS):
            pid, main = start_mousepad()
            before = {"active": "main" if active() == main else name(active()), "core_focus": "main" if core_focus() == main else name(core_focus())}
            if arm.startswith("driver"):
                reply = call("hotkey", {"keys": ["ctrl", "shift", "s"], "target": {"kind": "window", "pid": pid, "window_id": main}, "delivery_mode": "foreground"})
                summary = {k: reply.get(k) for k in ("ok", "error")}
                structured = (reply.get("result") or {}).get("structuredContent") or reply.get("structuredContent") or {}
                summary.update({k: v for k, v in structured.items() if "focus" in k or k in ("effect", "delivery", "status")})
            else:
                sh("xdotool", "key", "--clearmodifiers", "ctrl+shift+s")
                summary = {}
            after = watch(pid, main)
            row = {"arm": arm, "rep": rep + 1, "before": before, **after, "reply": summary}
            print(json.dumps(row))
            if rep == 0 and arm.startswith("driver"):
                print("full reply:", json.dumps(reply)[:2000])
            rows.append(row)
            close_dialogs(pid, main)
            sh("xdotool", "key", "--window", str(main), "ctrl+q")
            subprocess.run(["pkill", "-x", "mousepad"], env=ENV)
    print("\n| arm | reps | dialog opened | active window after 3 s | keyboard focus after 3 s |")
    print("|---|---|---|---|---|")
    for arm in ("driver hotkey, foreground", "xdotool key (control)"):
        mine = [r for r in rows if r["arm"] == arm]
        opened = sum(1 for r in mine if r["dialog"] not in ("", "-"))
        act = {k: sum(1 for r in mine if r["active"] == k) for k in {r["active"] for r in mine}}
        core = {k: sum(1 for r in mine if r["core_focus"] == k) for k in {r["core_focus"] for r in mine}}
        print(f"| {arm} | {len(mine)} | {opened} | {act} | {core} |")
    daemon.terminate()


if __name__ == "__main__":
    main_()
