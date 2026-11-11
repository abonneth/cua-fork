"""Which steps of a macOS existing-profile Chrome attach take the foreground from the user's app, per session."""

import faulthandler
import json
import os
import socket
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(line_buffering=True)
faulthandler.dump_traceback_later(2400, exit=True)
BIN, SOCK, VARIANT, BUILD = sys.argv[1:5]
REPS = int(os.environ.get("REPS", "5"))
SETTLE_S = 1.5
HOST = "TextEdit"
HOST_FILE = "/tmp/ab-host.txt"
CHROME = "/Applications/Google Chrome.app"
PROFILE = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "chrome-profile")
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots")
TYPING = os.environ.get("AB_TYPING") == "on"


def run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    print("step:", " ".join(cmd)[:160])
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {timeout}s: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, -1, "", "timeout")


class Watch:
    """Front app (polled every 3 ms), activation notifications, and the owner of the topmost normal window."""

    def __init__(self) -> None:
        built = run(["swiftc", "-O", os.path.join(HERE, "front_watch.swift"), "-o", "/tmp/front_watch"], 600)
        if built.returncode != 0:
            raise RuntimeError(f"swiftc failed: {built.stderr[-2000:]}")
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


class Typist:
    """A user typing x into the front app every 40 ms, through the HID event tap."""

    def __init__(self) -> None:
        self.sent = 0
        self.proc = subprocess.Popen(["/tmp/typist", "0.04"], stdout=subprocess.PIPE, text=True)
        self.thread = threading.Thread(target=self.count, daemon=True)
        self.thread.start()

    def count(self) -> None:
        assert self.proc.stdout
        for _ in self.proc.stdout:
            self.sent += 1

    def stop(self) -> int:
        self.proc.terminate()
        self.proc.wait(10)
        self.thread.join(5)
        return self.sent


def host_xs() -> int:
    return run(["osascript", "-e", 'tell application "TextEdit" to get text of document 1'], 15).stdout.count("x")


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
    print("call:", tool)
    return request({"method": "call", "name": tool, "args": args})


def structured(reply: dict) -> dict:
    result = reply.get("result") or {}
    return result.get("structuredContent") or {}


def shot(name: str) -> None:
    os.makedirs(SHOTS, exist_ok=True)
    run(["screencapture", "-x", os.path.join(SHOTS, name)], 20)


def launch_chrome(url: str) -> None:
    print("step: launch chrome", url)
    args = [f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", "--disable-features=MediaRouter", url]
    subprocess.Popen(
        [f"{CHROME}/Contents/MacOS/Google Chrome", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def edit_json(path: str, edit) -> None:
    with open(path) as handle:
        data = json.load(handle)
    edit(data)
    with open(path, "w") as handle:
        json.dump(data, handle)


def answer_local_network_prompt(timeout_s: float = 30) -> bool:
    """macOS asks once per app before Chrome's device discovery (chrome://inspect) reaches the local network."""
    call("start_session", {"session": "warmup"})
    try:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            windows = structured(call("list_windows", {"on_screen_only": True})).get("windows", [])
            prompt = next((w for w in windows if w.get("app_name") == "UserNotificationCenter"), None)
            if prompt:
                target = {"pid": prompt["pid"], "window_id": prompt["window_id"], "session": "warmup"}
                elements = structured(call("get_window_state", target)).get("elements") or []
                allow = next((e for e in elements if str(e.get("label", "")).strip() == "Allow"), None)
                if allow:
                    print("local network prompt: clicking Allow", json.dumps(call("click", {**target, "element_token": allow["element_token"]}))[:300])
                    time.sleep(2)
                    return True
                print("local network prompt without an Allow element:", [e.get("label") for e in elements][:20])
            time.sleep(1)
        return False
    finally:
        call("end_session", {"session": "warmup"})


def prepare_profile() -> None:
    local_state = os.path.join(PROFILE, "Local State")
    launch_chrome("chrome://inspect/#devices")
    for _ in range(60):
        if os.path.exists(local_state) and os.path.exists(os.path.join(PROFILE, "Default", "Preferences")):
            break
        time.sleep(1)
    else:
        shot("chrome-never-started.png")
        raise RuntimeError("Chrome never created its profile")
    print("answered local network prompt:", answer_local_network_prompt())
    shot("after-warmup.png")
    time.sleep(2)
    run(["pkill", "-x", "Google Chrome"])
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
    if TYPING:
        built = run(["swiftc", "-O", os.path.join(HERE, "typist.swift"), "-o", "/tmp/typist"], 600)
        if built.returncode != 0:
            raise RuntimeError(f"swiftc typist failed: {built.stderr[-2000:]}")
    print("chrome version:", run(["defaults", "read", f"{CHROME}/Contents/Info", "CFBundleShortVersionString"]).stdout.strip())

    def front_host() -> bool:
        run(["open", "-a", HOST, HOST_FILE], 15)
        time.sleep(1.0)
        return watch.current("front") == HOST

    def phase(fn, name, rep):
        fronted = front_host()
        t0 = time.time()
        if rep == 2:
            for at in (1.5, 4.0):
                threading.Timer(at, shot, args=(f"{VARIANT}-{BUILD}-{name}-{at}s.png",)).start()
        before = host_xs() if TYPING else 0
        typist = Typist() if TYPING else None
        out = fn()
        took = time.time() - t0
        time.sleep(SETTLE_S)
        t1 = time.time()
        keys = None
        if typist:
            sent = typist.stop()
            time.sleep(0.5)
            keys = {"sent": sent, "lost": sent - (host_xs() - before)}
        return out, {
            "fronted": fronted,
            "keys": keys,
            "took_s": round(took, 2),
            "front": watch.away("front", t0, t1),
            "top": watch.away("top", t0, t1),
            "activations": watch.activations(t0, t1),
        }

    if TYPING:
        _, control = phase(lambda: time.sleep(2), "control", 0)
        print("control (typing, no driver call):", json.dumps(control))
    rows = []
    for rep in range(1, REPS + 1):
        session = f"s{rep}"
        where = {"session": session, **chrome_window()}
        call("start_session", {"session": session})
        phases = {}
        prepare = {**where, "strategy": {"kind": "existing_profile"}}
        reply, phases["attach_background"] = phase(lambda: structured(call("browser_prepare", prepare)), "attach_background", rep)
        outcome = reply.get("status") or (reply.get("refusal") or {}).get("code") or json.dumps(reply)[:300]
        if (reply.get("refusal") or {}).get("code") == "background_unavailable":
            reply, phases["attach_foreground"] = phase(lambda: structured(call("browser_prepare", {**prepare, "delivery_mode": "foreground"})), "attach_foreground", rep)
            outcome += " -> foreground " + (reply.get("status") or json.dumps(reply)[:600])
        effects = reply.get("side_effects") or (reply.get("detail") or {}).get("setup_side_effects") or {}
        state = structured(call("get_browser_state", where))
        bound = bool(state.get("target_id"))
        if not bound and rep == 1:
            shot(f"unbound-{VARIANT}-{BUILD}.png")
            print("prepare reply:", json.dumps(reply)[:2000])
        tab = next((t["tab_id"] for t in state.get("tabs", []) if t.get("active")), None)
        if tab:
            _, phases["act"] = phase(lambda: call("browser_navigate", {**where, "target_id": state["target_id"], "tab_id": tab, "url": f"https://example.com/?rep={rep}"}), "act", rep)
        ended, phases["end_session"] = phase(lambda: structured(call("end_session", {"session": session})), "end_session", rep)
        done = sorted(k for k, v in effects.items() if v is True)
        print(f"rep {rep}: {outcome} bound={bound} side_effects={done} end={json.dumps(ended)[:200]}")
        for name, seen in phases.items():
            print(f"  {name}: {json.dumps(seen)}")
        rows.append(phases)

    lines = [
        f"### {VARIANT} / build {BUILD} / typing {'on' if TYPING else 'off'}",
        "",
        "| phase | reps Chrome took the front app | ms away (front app) | reps Chrome window on top | ms on top | median call s | keys lost/sent |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in ("attach_background", "attach_foreground", "act", "end_session"):
        seen = [r[name] for r in rows if name in r]
        if not seen:
            continue
        front = [sum(ms for w, ms in s["front"] if "Chrome" in w) for s in seen]
        top = [sum(ms for w, ms in s["top"] if "Chrome" in w) for s in seen]
        took = sorted(s["took_s"] for s in seen)[len(seen) // 2]
        keys = [f"{s['keys']['lost']}/{s['keys']['sent']}" for s in seen if s.get("keys")]
        lines.append(
            f"| {name} | {sum(1 for f in front if f)}/{len(seen)} | {front} | {sum(1 for t in top if t)}/{len(seen)} | {top} | {took} | {', '.join(keys) or '-'} |"
        )
    summary = "\n".join(lines)
    print(summary)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as out:
        out.write(summary + "\n")


main()
