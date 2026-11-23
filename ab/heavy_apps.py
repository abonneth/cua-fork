"""Walk a heavy app's window the way `cua state` does, many times, and record cost and memory per walk."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

import psutil

SOCK = sys.argv[1]
LABEL = sys.argv[2]
LAUNCH = sys.argv[3]
MATCH = re.compile(sys.argv[4], re.I)
WALKS = int(os.environ.get("AB_WALKS", "30"))
BIG_WALKS = int(os.environ.get("AB_BIG_WALKS", "4"))
OUT = os.environ.get("AB_OUT", "ab-results")
SESSION = "ab-heavy"
WINDOWS = os.name == "nt"
STATE_ARGS = {"include_screenshot": True, "max_elements": 2000, "timeout_ms": 15000, "tree_format": "both", "verbose": True}


def request(payload: dict, timeout: float = 120) -> dict:
    line = (json.dumps(payload) + "\n").encode()
    if WINDOWS:
        deadline = time.monotonic() + 10
        while True:
            try:
                pipe = open(SOCK, "r+b", buffering=0)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        with pipe:
            pipe.write(line)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = pipe.read(1 << 16)
                if not chunk:
                    break
                buf += chunk
    else:
        import socket

        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(timeout)
            conn.connect(SOCK)
            conn.sendall(line)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
    return json.loads(buf or b"{}")


def call(tool: str, args: dict, timeout: float = 120) -> tuple[dict, int, float]:
    started = time.monotonic()
    reply = request({"method": "call", "name": tool, "args": {"session": SESSION, **args}, "client_kind": "cli"}, timeout)
    return reply, len(json.dumps(reply)), time.monotonic() - started


def structured(reply: dict) -> dict:
    result = reply.get("result") or {}
    return result.get("structuredContent") or {}


def driver_procs() -> list[psutil.Process]:
    return [p for p in psutil.process_iter(["name"]) if "cua-driver" in (p.info["name"] or "").lower()]


def app_procs(pid: int | None) -> list[psutil.Process]:
    found = []
    for p in psutil.process_iter(["name", "cmdline"]):
        text = (p.info["name"] or "") + " " + " ".join(p.info["cmdline"] or [])
        if (pid is not None and p.pid == pid) or (MATCH.search(text) and "heavy_apps.py" not in text):
            found.append(p)
    return found


def rss_mb(procs: list[psutil.Process]) -> float:
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except psutil.Error:
            pass
    return round(total / 2**20, 1)


def cpu_s(procs: list[psutil.Process]) -> float:
    total = 0.0
    for p in procs:
        try:
            t = p.cpu_times()
            total += t.user + t.system
        except psutil.Error:
            pass
    return total


def find_window(deadline_s: float) -> dict | None:
    deadline = time.monotonic() + deadline_s
    pids = set()
    while time.monotonic() < deadline:
        pids = {p.pid for p in app_procs(None)}
        reply, _, _ = call("list_windows", {})
        windows = [w for w in structured(reply).get("windows") or [] if w.get("pid") in pids or MATCH.search(str(w.get("app_name") or ""))]
        windows = [w for w in windows if (w.get("bounds") or {}).get("width", 0) > 300 and w.get("is_on_screen", True)]
        if windows:
            time.sleep(8)
            reply, _, _ = call("list_windows", {})
            fresh = [w for w in structured(reply).get("windows") or [] if w.get("window_id") in {x["window_id"] for x in windows}]
            pool = fresh or windows
            return max(pool, key=lambda w: w["bounds"]["width"] * w["bounds"]["height"])
        time.sleep(3)
    print("no window; app processes:", [(p.pid, p.info["name"]) for p in app_procs(None)])
    print("windows:", json.dumps(structured(call("list_windows", {})[0]))[:3000])
    return None


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    rows: list[dict] = []
    call("start_session", {})
    print(f"== {LABEL}: launching {LAUNCH}")
    proc = subprocess.Popen(LAUNCH if WINDOWS else shlex.split(LAUNCH), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=WINDOWS)
    window = find_window(240)
    summary = {"label": LABEL, "window": window, "rows": rows}
    if window is None:
        summary["error"] = "no window"
    else:
        print(f"window {window['window_id']} pid {window['pid']} {window.get('app_name')!r} {window.get('title')!r} {window['bounds']}")
        target = {"pid": window["pid"], "window_id": window["window_id"]}
        shot = os.path.join(tempfile.gettempdir(), "ab-shot.png")
        apps, drivers = app_procs(window["pid"]), driver_procs()
        base = {"app_rss": rss_mb(apps), "driver_rss": rss_mb(drivers), "avail_mb": round(psutil.virtual_memory().available / 2**20)}
        summary["baseline"] = base
        print("baseline", base)
        plan = [("std", WALKS, {}), ("big", BIG_WALKS, {"max_elements": 20000, "timeout_ms": 25000})]
        for phase, count, extra in plan:
            for i in range(count):
                apps, drivers = app_procs(window["pid"]), driver_procs()
                app_cpu, drv_cpu = cpu_s(apps), cpu_s(drivers)
                try:
                    reply, size, wall = call("get_window_state", {**target, **STATE_ARGS, **extra, "screenshot_out_file": shot}, timeout=90)
                    s = structured(reply)
                    err = "" if not (reply.get("result") or {}).get("isError") and reply.get("ok", True) else json.dumps(reply)[:300]
                except Exception as exc:
                    s, size, wall, err = {}, 0, 90.0, f"{type(exc).__name__}: {exc}"
                row = {
                    "phase": phase,
                    "i": i,
                    "wall_s": round(wall, 2),
                    "walk_ms": s.get("walk_elapsed_ms"),
                    "visited": s.get("nodes_visited"),
                    "pending": s.get("nodes_pending"),
                    "elements": len(s.get("elements") or []),
                    "md_kb": round(len(str(s.get("tree_markdown") or "")) / 1024, 1),
                    "reply_kb": round(size / 1024, 1),
                    "trunc": s.get("truncation_reason") or ("yes" if s.get("truncated") else ""),
                    "degraded": s.get("degraded_reason") or "",
                    "app_rss": rss_mb(apps),
                    "driver_rss": rss_mb(drivers),
                    "app_cpu_s": round(cpu_s(apps) - app_cpu, 2),
                    "driver_cpu_s": round(cpu_s(drivers) - drv_cpu, 2),
                    "avail_mb": round(psutil.virtual_memory().available / 2**20),
                    "alive": proc.poll() is None or bool(app_procs(window["pid"])),
                    "err": err,
                }
                rows.append(row)
                print(json.dumps(row))
                if not row["alive"]:
                    summary["error"] = "app died"
                    break
        idle_until = time.monotonic() + float(os.environ.get("AB_IDLE_S", "20"))
        while time.monotonic() < idle_until and (proc.poll() is None or app_procs(window["pid"])):
            time.sleep(2)
        if not (proc.poll() is None or app_procs(window["pid"])):
            summary["error"] = "app died"
        apps, drivers = app_procs(window["pid"]), driver_procs()
        summary["after_idle"] = {"app_rss": rss_mb(apps), "driver_rss": rss_mb(drivers), "avail_mb": round(psutil.virtual_memory().available / 2**20)}
    with open(os.path.join(OUT, f"{LABEL}.json"), "w") as handle:
        json.dump(summary, handle, indent=1)
    for p in app_procs(None):
        try:
            p.kill()
        except psutil.Error:
            pass
    report(summary)


def report(summary: dict) -> None:
    rows = summary["rows"]
    std = [r for r in rows if r["phase"] == "std"]
    big = [r for r in rows if r["phase"] == "big"]
    base = summary.get("baseline") or {}
    idle = summary.get("after_idle") or {}

    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else None

    line = (
        f"| {summary['label']} | {summary.get('error', '')} | {med([r['wall_s'] for r in std])} | {med([r['visited'] for r in std])} | "
        f"{','.join(sorted({r['trunc'] for r in std if r['trunc']}))} | {base.get('app_rss')} -> {std[-1]['app_rss'] if std else ''} -> {idle.get('app_rss')} | "
        f"{base.get('driver_rss')} -> {std[-1]['driver_rss'] if std else ''} -> {idle.get('driver_rss')} | "
        f"{med([r['wall_s'] for r in big])} / {med([r['visited'] for r in big])} / {big[-1]['app_rss'] if big else ''} | "
        f"{sum(1 for r in rows if r['err'])} |"
    )
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


if __name__ == "__main__":
    main()
