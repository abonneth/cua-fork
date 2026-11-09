"""A/B: console windows a console-less daemon opens while it serves the calls that spawn PowerShell or cmd."""

import json
import os
import subprocess
import sys
import time

from driver import DETACHED_PROCESS, Daemon, Watcher, consoles

REPS = 10
BINS = {"A": sys.argv[1], "B": sys.argv[2]}
PHASES = ("startup", "check_permissions", "health_report", "browser attach")


def browser_target(daemon: Daemon) -> dict:
    listed = daemon.call("list_windows", {"on_screen_only": True})
    windows = (listed.get("result") or {}).get("structuredContent", {}).get("windows", [])
    edge = next(w for w in windows if "edge" in str(w.get("app_name", "")).lower())
    return {"pid": edge["pid"], "window_id": edge["window_id"]}


def attach(daemon: Daemon, where: dict) -> str:
    reply = daemon.call("get_browser_state", {"session": "ab", **where})
    text = json.dumps(reply)
    if "browser_consent_required" in text:
        daemon.call("browser_prepare", {"session": "ab", **where, "strategy": {"kind": "existing_profile"}})
        reply = daemon.call("get_browser_state", {"session": "ab", **where})
    structured = (reply.get("result") or {}).get("structuredContent") or {}
    return "ok" if structured.get("target_id") or structured.get("tabs") else json.dumps(reply)[:200]


def rep(variant: str, index: int) -> dict[str, list[str]]:
    exe = os.path.join(BINS[variant], "cua-driver.exe")
    seen: dict[str, list[str]] = {}
    watcher = Watcher()
    daemon = Daemon(exe, rf"\\.\pipe\ab-{variant}-{index}")
    seen["startup"] = watcher.stop()
    try:
        daemon.call("start_session", {"session": "ab"})
        where = browser_target(daemon)
        for phase, run in (
            ("check_permissions", lambda: daemon.call("check_permissions", {})),
            ("health_report", lambda: daemon.call("health_report", {})),
            ("browser attach", lambda: attach(daemon, where)),
        ):
            watcher = Watcher()
            outcome = run()
            seen[phase] = watcher.stop()
            if phase == "browser attach" and outcome != "ok":
                print(f"  {variant}#{index} browser attach: {outcome}")
    finally:
        daemon.stop()
    return seen


def control() -> list[str]:
    """The detector itself: a console-less process that starts `cmd` without CREATE_NO_WINDOW must show one."""
    watcher = Watcher()
    child = "import subprocess; subprocess.run(['cmd', '/c', 'ping -n 2 127.0.0.1 >nul'])"
    subprocess.run([sys.executable, "-c", child], creationflags=DETACHED_PROCESS)
    time.sleep(0.3)
    return watcher.stop()


print("detector control (DETACHED python -> cmd):", control())
totals = {v: {p: 0 for p in PHASES} for v in BINS}
reps_with = {v: {p: 0 for p in PHASES} for v in BINS}
examples: dict[str, set[str]] = {v: set() for v in BINS}
others: dict[str, set[str]] = {v: set() for v in BINS}
for index in range(REPS):
    for variant in BINS:
        seen = rep(variant, index)
        for phase, windows in seen.items():
            found = consoles(windows)
            totals[variant][phase] += len(found)
            reps_with[variant][phase] += bool(found)
            examples[variant].update(found)
            others[variant].update(w for w in windows if w not in found)
        print(f"rep {index} {variant}: " + ", ".join(f"{p}={len(consoles(seen[p]))}" for p in PHASES))

lines = ["| call | A consoles (reps with any) | B consoles (reps with any) |", "|---|---|---|"]
for phase in PHASES:
    lines.append(
        f"| {phase} | {totals['A'][phase]} ({reps_with['A'][phase]}/{REPS}) | {totals['B'][phase]} ({reps_with['B'][phase]}/{REPS}) |"
    )
summary = "\n".join(lines)
print(summary)
for variant in BINS:
    print(f"{variant} console windows: {sorted(examples[variant])}")
    print(f"{variant} other new windows: {sorted(others[variant])}")
with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
    out.write(summary + "\n")
print("RESULTS_JSON " + json.dumps({"totals": totals, "reps_with": reps_with}))
