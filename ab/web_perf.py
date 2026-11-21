"""Wall time of each browser tool call `cua web` makes, on an existing-profile Chrome bound by the daemon, with effect checks."""

import http.server
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(line_buffering=True)
BIN, SOCK, BUILD = sys.argv[1:4]
REPS = int(os.environ.get("REPS", "20"))
CHROME = "/Applications/Google Chrome.app"
PROFILE = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "chrome-profile")
PAGE_PORT = 8765
PAGE = f"http://127.0.0.1:{PAGE_PORT}/"
HTML = b"""<!doctype html><title>perf page</title>
<h1>Perf page</h1>
<button id="bump" onclick="n.textContent = +n.textContent + 1">Bump</button>
<p>Clicks: <span id="n">0</span></p>
<button id="fetch" onclick="setTimeout(() => { m.textContent = +m.textContent + 1 }, 150)">Fetch</button>
<p>Fetched: <span id="m">0</span></p>
<button id="toggle" aria-pressed="false" onclick="this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')">Toggle</button>
<button id="save" aria-pressed="false" onclick="const note = document.getElementById('note'); note.textContent = 'saving'; setTimeout(() => { this.setAttribute('aria-pressed', this.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); note.textContent = 'saved' }, 100)">Save</button>
<p id="note">idle</p>
<label>Name <input id="name" aria-label="Name"></label>
<a href="/">Reload link</a>"""


class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *args) -> None:
        pass


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
    return request({"method": "call", "name": tool, "args": {"session": "perf", **args}})


def structured(reply: dict) -> dict:
    return (reply.get("result") or {}).get("structuredContent") or {}


def run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def launch_chrome(url: str) -> None:
    args = [f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", "--disable-features=MediaRouter", url]
    subprocess.Popen([f"{CHROME}/Contents/MacOS/Google Chrome", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def chrome_window() -> dict:
    for _ in range(60):
        windows = structured(call("list_windows", {"on_screen_only": True})).get("windows", [])
        chromes = [w for w in windows if "chrome" in str(w.get("app_name", "")).lower() and w.get("title")]
        if chromes:
            best = max(chromes, key=lambda w: (w.get("bounds") or {}).get("width", 0) * (w.get("bounds") or {}).get("height", 0))
            return {"pid": best["pid"], "window_id": best["window_id"]}
        time.sleep(1)
    raise RuntimeError("no Chrome window")


def answer_local_network_prompt(where: dict) -> None:
    """macOS asks once per app before Chrome's device discovery reaches the local network."""
    deadline = time.time() + 30
    while time.time() < deadline:
        windows = structured(call("list_windows", {"on_screen_only": False})).get("windows", [])
        prompt = next((w for w in windows if "usernotification" in str(w.get("app_name", "")).lower()), None)
        if prompt:
            target = {"pid": prompt["pid"], "window_id": prompt["window_id"]}
            elements = structured(call("get_window_state", target | {"tree_format": "elements"})).get("elements") or []
            allow = next((e for e in elements if str(e.get("label", "")).strip() == "Allow"), None)
            if allow:
                call("click", {**target, "element_token": allow["element_token"]})
                print("local network prompt: allowed")
                return
        time.sleep(1)


def attach() -> dict:
    launch_chrome(PAGE)
    time.sleep(8)
    where = chrome_window()
    call("start_session", {})
    prepared = structured(call("browser_prepare", {**where, "strategy": {"kind": "existing_profile"}, "delivery_mode": "foreground"}))
    print("prepare:", json.dumps(prepared)[:300])
    if prepared.get("status") != "prepared":
        answer_local_network_prompt(where)
        prepared = structured(call("browser_prepare", {**where, "strategy": {"kind": "existing_profile"}, "delivery_mode": "foreground"}))
        print("prepare again:", json.dumps(prepared)[:300])
    bound = structured(call("get_browser_state", where))
    tab = next((t for t in bound.get("tabs") or [] if t.get("active")), (bound.get("tabs") or [{}])[0])
    target = {"target_id": bound.get("target_id"), "tab_id": tab.get("tab_id")}
    print("bound:", target, "mutation_allowed:", bound.get("mutation_allowed"), "quality:", bound.get("binding_quality"))
    if not target["target_id"] or not target["tab_id"]:
        raise RuntimeError(f"bind failed: {json.dumps(bound)[:600]}")
    return target


def refs_of(state: dict) -> dict[str, str]:
    found = {}
    for ref in state.get("refs") or []:
        name = str(ref.get("name") or ref.get("label") or "")
        if name in NAMES and ref.get("ref"):
            found[name] = ref["ref"]
    if len(found) != len(NAMES):
        raise RuntimeError(f"refs not found: {json.dumps(state)[:1200]}")
    return found


def refs(target: dict) -> dict[str, str]:
    return refs_of(structured(call("get_browser_state", {**target, "snapshot_format": "semantic_v2"})))


def page_text(target: dict) -> str:
    return json.dumps(structured(call("get_browser_state", {**target, "snapshot_format": "semantic_v2"})))


NAMES = ("Bump", "Fetch", "Toggle", "Save", "Name")
REFUSALS: dict[str, int] = {}
EFFECTS: dict[str, dict[str, int]] = {}


def effect_of(value) -> str | None:
    if isinstance(value, dict):
        if isinstance(value.get("effect"), str):
            return value["effect"]
        for inner in value.values():
            found = effect_of(inner)
            if found:
                return found
    if isinstance(value, list):
        for inner in value:
            found = effect_of(inner)
            if found:
                return found
    return None


def timed(samples: dict, name: str, tool: str, args: dict) -> dict:
    started = time.perf_counter()
    reply = call(tool, args)
    samples.setdefault(name, []).append((time.perf_counter() - started) * 1000)
    content = structured(reply)
    if tool == "browser_click":
        effect = effect_of(content) or "none"
        EFFECTS.setdefault(name, {})[effect] = EFFECTS.setdefault(name, {}).get(effect, 0) + 1
        if sum(EFFECTS[name].values()) == 1:
            print(f"{name} reply:", json.dumps(content)[:700])
    if not reply.get("ok", False) or (reply.get("result") or {}).get("isError") or content.get("refusal") or content.get("status") == "refused":
        REFUSALS[name] = REFUSALS.get(name, 0) + 1
        if REFUSALS[name] == 1:
            print(f"{name} failed:", json.dumps(reply)[:800])
    return content


def main() -> None:
    threading.Thread(target=http.server.ThreadingHTTPServer(("127.0.0.1", PAGE_PORT), Page).serve_forever, daemon=True).start()
    print(run([BIN, "--version"]).stdout.strip(), "build", BUILD)
    target = attach()
    call("browser_navigate", {**target, "url": PAGE})
    time.sleep(1)
    found = refs(target)
    samples: dict[str, list[float]] = {}
    for rep in range(REPS):
        timed(samples, "browser_dialog inspect", "browser_dialog", {**target, "action": "inspect"})
        found = refs_of(timed(samples, "get_browser_state", "get_browser_state", {**target, "snapshot_format": "semantic_v2"}))
        timed(samples, "click: counter elsewhere (sync)", "browser_click", {**target, "ref": found["Bump"], "input_route": "dom_event"})
        timed(samples, "click: counter elsewhere (after 150 ms)", "browser_click", {**target, "ref": found["Fetch"], "input_route": "dom_event"})
        timed(samples, "click: own aria-pressed (sync)", "browser_click", {**target, "ref": found["Toggle"], "input_route": "dom_event"})
        timed(samples, "click: text elsewhere, then own aria-pressed after 100 ms", "browser_click", {**target, "ref": found["Save"], "input_route": "dom_event"})
        time.sleep(0.3)
        found = refs_of(structured(call("get_browser_state", {**target, "snapshot_format": "semantic_v2"})))
        timed(samples, "click: counter elsewhere (sync), trusted route", "browser_click", {**target, "ref": found["Bump"], "input_route": "trusted"})
        timed(samples, "browser_type", "browser_type", {**target, "ref": found["Name"], "text": f"rep{rep}", "replace": True})
    time.sleep(1)
    text = page_text(target)
    clicks_ok = f'"name": "{2 * REPS}"' in text and f'"name": "{REPS}"' in text
    typed_ok = f"rep{REPS - 1}" in text
    for rep in range(max(3, REPS // 4)):
        timed(samples, "browser_navigate", "browser_navigate", {**target, "url": PAGE})
        found = refs(target)
    print(f"\neffects: {REPS} clicks counted: {clicks_ok}; last typed text present: {typed_ok}; refusals: {REFUSALS or 'none'}; click effects: {EFFECTS}")
    print(f"\n| build {BUILD} | n | median ms | p90 ms | min ms |")
    print("|---|---|---|---|---|")
    for name, values in samples.items():
        values.sort()
        p90 = values[min(len(values) - 1, int(len(values) * 0.9))]
        print(f"| {name} | {len(values)} | {statistics.median(values):.0f} | {p90:.0f} | {values[0]:.0f} |")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"perf-{BUILD}.json"), "w") as handle:
        json.dump({"build": BUILD, "effects": {"clicks": clicks_ok, "typed": typed_ok}, "refusals": REFUSALS, "samples": samples}, handle)
    if not (clicks_ok and typed_ok) or REFUSALS:
        print("effect check failed; page:", text[:1500])
        sys.exit(1)


if __name__ == "__main__":
    main()
