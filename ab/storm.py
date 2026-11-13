"""Two cua-driver daemons from one executable: CPU of replayd and of both daemons while they coexist."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BINS = Path(os.environ.get("AB_BINS", "/tmp/cua-mem/bins"))
HOLD = int(os.environ.get("AB_HOLD", "40"))
ENV = {
    **os.environ,
    "CUA_DRIVER_EMBEDDED": "1",
    "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false",
    "CUA_DRIVER_RS_UPDATE_CHECK": "false",
}


def start(build: str) -> tuple[subprocess.Popen, str, str, Path]:
    binary = str(BINS / f"cua-driver-{build}")
    work = Path(tempfile.mkdtemp(prefix="cua-storm-"))
    sock = str(work / "d.sock")
    log = (work / "serve.log").open("w")
    daemon = subprocess.Popen(
        [binary, "serve", "--socket", sock, "--pid-file", str(work / "d.pid"), "--embedded", "--no-overlay"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        env=ENV,
        start_new_session=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not Path(sock).exists():
        time.sleep(0.2)
    return daemon, binary, sock, work


def shot(binary: str, sock: str, work: Path, tool: str = "desktop") -> tuple[bool, float]:
    args = {"screenshot_out_file": str(work / "shot.png")}
    name = "get_desktop_state"
    if tool == "window":
        listed = subprocess.run([binary, "call", "--socket", sock, "list_windows"], capture_output=True, text=True, timeout=60, env=ENV)
        windows = [w for w in json.loads(listed.stdout).get("windows", []) if w.get("is_on_screen") and w.get("bounds", {}).get("height", 0) > 300]
        target = max(windows, key=lambda w: w["bounds"]["width"] * w["bounds"]["height"])
        args |= {"pid": target["pid"], "window_id": target["window_id"]}
        name = "get_window_state"
    started = time.monotonic()
    try:
        done = subprocess.run([binary, "call", "--socket", sock, name, json.dumps(args)], capture_output=True, text=True, timeout=120, env=ENV)
        ok = done.returncode == 0 and '"isError": true' not in done.stdout and (work / "shot.png").exists()
    except subprocess.TimeoutExpired:
        ok = False
    (work / "shot.png").unlink(missing_ok=True)
    return ok, (time.monotonic() - started) * 1000


def cpu(pids: dict[str, int], seconds: int = 6) -> str:
    out = subprocess.run(["top", "-l", "2", "-s", str(seconds), "-stats", "pid,cpu"], capture_output=True, text=True).stdout
    last = out.split("PID")[-1]
    found = {}
    for name, pid in pids.items():
        row = re.search(rf"^{pid}\s+([\d.]+)", last, re.M)
        found[name] = f"{row.group(1)}%/{footprint(pid)}" if row else "gone"
    return " ".join(f"{name}={value}" for name, value in found.items())


def footprint(pid: int) -> str:
    out = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True).stdout
    total = re.search(r"phys_footprint: ([\d.]+) (\w+)", out)
    return f"{total.group(1)}{total.group(2)}" if total else "?"


def main() -> None:
    specs = [spec.split(":") + ["desktop"] for spec in sys.argv[1:]]
    builds = [(spec[0], spec[1]) for spec in specs]
    replayd = int(subprocess.run(["pgrep", "-x", "replayd"], capture_output=True, text=True).stdout.split()[0])
    daemons = []
    try:
        for index, (build, tool) in enumerate(builds, 1):
            daemon, binary, sock, work = start(build)
            ok, elapsed = shot(binary, sock, work, tool)
            daemons.append((daemon, binary, sock, work, build, tool))
            print(f"#{index} {build} pid={daemon.pid} first {tool} capture ok={ok} {elapsed:.0f} ms", flush=True)
            pids = {"replayd": replayd} | {f"#{i}": d[0].pid for i, d in enumerate(daemons, 1)}
            print(f"   idle CPU with {index} daemon(s): {cpu(pids)}", flush=True)
        pids = {"replayd": replayd} | {f"#{i}": d[0].pid for i, d in enumerate(daemons, 1)}
        waited = 0
        while waited < HOLD:
            time.sleep(10)
            waited += 16
            print(f"   idle CPU after {waited} s more: {cpu(pids)}", flush=True)
        for index, (daemon, binary, sock, work, build, tool) in enumerate(daemons, 1):
            ok, elapsed = shot(binary, sock, work, tool)
            text = (work / "serve.log").read_text()
            status = daemon.poll()
            print(
                f"#{index} {build} later {tool} capture ok={ok} {elapsed:.0f} ms"
                f" sck_timeouts={text.count('timed out after')} sck_blocked={text.count('still blocked')} claimed_elsewhere={text.count('in use by')}"
                f" exit={'alive' if status is None else status}",
                flush=True,
            )
        if len(daemons) > 1:
            gone = daemons.pop(0 if os.environ.get("AB_KILL") == "first" else -1)
            gone[0].terminate()
            gone[0].wait(timeout=10)
            pids = {"replayd": replayd} | {"left": daemons[0][0].pid}
            time.sleep(2)
            print(f"   idle CPU once the {'first' if os.environ.get('AB_KILL') == 'first' else 'last'} daemon is stopped: {cpu(pids)}", flush=True)
            daemon, binary, sock, work, build, tool = daemons[0]
            before = (work / "serve.log").read_text().count("in use by")
            runs = [shot(binary, sock, work, tool) for _ in range(3)]
            after = (work / "serve.log").read_text().count("in use by")
            print(f"   the daemon left: 3 more {tool} captures ok={[ok for ok, _ in runs]} ms={[round(ms) for _, ms in runs]} new 'in use by' log lines={after - before}", flush=True)
    finally:
        for daemon, *_ in daemons:
            if daemon.poll() is None:
                daemon.terminate()
        for daemon, *_ in daemons:
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()


main()
