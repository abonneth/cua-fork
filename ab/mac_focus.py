"""Which steps of a macOS existing-profile Chrome attach take the foreground from the user's app, per session."""

import json
import os
import socket
import subprocess
import sys
import threading
import time

BIN, SOCK, VARIANT, BUILD = sys.argv[1:5]
REPS = int(os.environ.get("REPS", "5"))
SETTLE_S = 1.5
HOST = "TextEdit"
HOST_FILE = "/tmp/ab-host.txt"
CHROME = "/Applications/Google Chrome.app"
PROFILE = os.path.expanduser("~/Library/Application Support/Google/Chrome")
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots")


class Watch:
    """Front app (polled every 3 ms), activation notifications, and the owner of the topmost normal window."""

    def __init__(self) -> None:
        subprocess.run(["swiftc", "-O", os.path.join(HERE, "front_watch.swift"), "-o", "/tmp/front_watch"], check=True)
        self.events: list[tuple[float, str, str]] = []
        self.proc = subprocess.Popen(["/tmp/front_watch"], stdout=subprocess.PIPE, text=True)
        ready = threading.Event()

        def read() -> None:
            assert self.proc.stdout
            for line in self.proc.stdout:
                t, kind, who = line.rstrip("\n").split("\t", 2)
                if kind == "ready":
                    ready.set()
                else:
                    self.events.append((float(t), kind, who))

        threading.Thread(target=read, daemon=True).start()
        if not ready.wait(60):
            raise TimeoutError("front_watch never started")

    def current(self, kind: str) -> str:
        found = [w for _, k, w in self.events if k == kind]
        return found[-1] if found else "?"

    def away(self, kind: str, t0: float, t1: float) -> list[tuple[str, int]]:
        stream = [(t, w) for t, k, w in list(self.events) if k == kind]
        prior = [w for t, w in stream if t <= t0]
        holder, since = (prior[-1] if prior else "?"), t0
        spans = []
        for t, w in stream:
            if t <= t0 or t >= t1:
                continue
            if holder != HOST:
                spans.append((holder, t - since))
            holder, since = w, t
        if holder != HOST:
            spans.append((holder, t1 - since))
        return [(w, round(d * 1000)) for w, d in spans if d > 0.0005]

    def activations(self, t0: float, t1: float) -> list[str]:
        return [w for t, k, w in list(self.events) if k == "active" and t0 < t < t1 and w != HOST]


def request(payload: dict) -> dict:
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(180)
        conn.connect(SOCK)
        conn.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf or b"{}")


def call(tool: str, args: dict) -> dict:
    return request({"method": "call", "name": tool, "args": args})


def structured(reply: dict) -> dict:
    result = reply.get("result") or {}
    return result.get("structuredContent") or {}


def shot(name: str) -> None:
    os.makedirs(SHOTS, exist_ok=True)
    subprocess.run(["screencapture", "-x", os.path.join(SHOTS, name)], capture_output=True)


def launch_chrome(url: str) -> None:
    subprocess.run(["open", "-na", CHROME, "--args", "--no-first-run", "--no-default-browser-check", url], check=True)


def edit_json(path: str, edit) -> None:
    with open(path) as handle:
        data = json.load(handle)
    edit(data)
    with open(path, "w") as handle:
        json.dump(data, handle)


def prepare_profile() -> None:
    local_state = os.path.join(PROFILE, "Local State")
    launch_chrome("about:blank")
    for _ in range(60):
        if os.path.exists(local_state) and os.path.exists(os.path.join(PROFILE, "Default", "Preferences")):
            break
        time.sleep(1)
    time.sleep(5)
    subprocess.run(["pkill", "-x", "Google Chrome"])
    time.sleep(4)

    def clean_exit(prefs: dict) -> None:
        prefs.setdefault("profile", {}).update({"exit_type": "Normal", "exited_cleanly": True})

    edit_json(os.path.join(PROFILE, "Default", "Preferences"), clean_exit)
    if VARIANT == "remote-debugging-already-on":
        edit_json(local_state, lambda s: s.setdefault("devtools", {}).setdefault("remote_debugging", {}).update({"user-enabled": True}))
    with open(local_state) as handle:
        print("Local State devtools:", json.dumps(json.load(handle).get("devtools")))
    launch_chrome("https://example.com")
    time.sleep(10)
    print("DevToolsActivePort before the probe:", os.path.exists(os.path.join(PROFILE, "DevToolsActivePort")))


def chrome_window() -> dict:
    windows = structured(call("list_windows", {"on_screen_only": True})).get("windows", [])
    chromes = [w for w in windows if "chrome" in str(w.get("app_name", "")).lower() and w.get("title")]
    best = max(chromes, key=lambda w: (w.get("bounds") or {}).get("width", 0) * (w.get("bounds") or {}).get("height", 0))
    return {"pid": best["pid"], "window_id": best["window_id"]}


def main() -> None:
    with open(HOST_FILE, "w") as handle:
        handle.write("the user's document\n")
    prepare_profile()
    watch = Watch()
    print("chrome version:", subprocess.run(["defaults", "read", f"{CHROME}/Contents/Info", "CFBundleShortVersionString"], capture_output=True, text=True).stdout.strip())

    def front_host() -> bool:
        subprocess.run(["open", "-a", HOST, HOST_FILE])
        time.sleep(1.0)
        return watch.current("front") == HOST

    def phase(fn):
        fronted = front_host()
        t0 = time.time()
        out = fn()
        took = time.time() - t0
        time.sleep(SETTLE_S)
        t1 = time.time()
        return out, {
            "fronted": fronted,
            "took_s": round(took, 2),
            "front": watch.away("front", t0, t1),
            "top": watch.away("top", t0, t1),
            "activations": watch.activations(t0, t1),
        }

    rows = []
    for rep in range(1, REPS + 1):
        session = f"s{rep}"
        where = {"session": session, **chrome_window()}
        call("start_session", {"session": session})
        phases = {}
        prepare = {**where, "strategy": {"kind": "existing_profile"}}
        reply, phases["attach_background"] = phase(lambda: structured(call("browser_prepare", prepare)))
        outcome = reply.get("status") or (reply.get("refusal") or {}).get("code") or json.dumps(reply)[:300]
        if (reply.get("refusal") or {}).get("code") == "background_unavailable":
            reply, phases["attach_foreground"] = phase(lambda: structured(call("browser_prepare", {**prepare, "delivery_mode": "foreground"})))
            outcome += " -> foreground " + (reply.get("status") or json.dumps(reply)[:600])
        effects = reply.get("side_effects") or (reply.get("detail") or {}).get("setup_side_effects") or {}
        state = structured(call("get_browser_state", where))
        bound = bool(state.get("target_id"))
        if not bound and rep == 1:
            shot(f"unbound-{VARIANT}-{BUILD}.png")
            print("prepare reply:", json.dumps(reply)[:2000])
        tab = next((t["tab_id"] for t in state.get("tabs", []) if t.get("active")), None)
        if tab:
            _, phases["act"] = phase(lambda: call("browser_navigate", {**where, "target_id": state["target_id"], "tab_id": tab, "url": f"https://example.com/?rep={rep}"}))
        ended, phases["end_session"] = phase(lambda: structured(call("end_session", {"session": session})))
        done = sorted(k for k, v in effects.items() if v is True)
        print(f"rep {rep}: {outcome} bound={bound} side_effects={done} end={json.dumps(ended)[:200]}")
        for name, seen in phases.items():
            print(f"  {name}: {json.dumps(seen)}")
        rows.append(phases)

    lines = [
        f"### {VARIANT} / build {BUILD}",
        "",
        "| phase | reps Chrome took the front app | ms away (front app) | reps Chrome window on top | ms on top | median call s |",
        "|---|---|---|---|---|---|",
    ]
    for name in ("attach_background", "attach_foreground", "act", "end_session"):
        seen = [r[name] for r in rows if name in r]
        if not seen:
            continue
        front = [sum(ms for w, ms in s["front"] if "Chrome" in w) for s in seen]
        top = [sum(ms for w, ms in s["top"] if "Chrome" in w) for s in seen]
        took = sorted(s["took_s"] for s in seen)[len(seen) // 2]
        lines.append(
            f"| {name} | {sum(1 for f in front if f)}/{len(seen)} | {front} | {sum(1 for t in top if t)}/{len(seen)} | {top} | {took} |"
        )
    summary = "\n".join(lines)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as out:
        out.write(summary + "\n")


main()
