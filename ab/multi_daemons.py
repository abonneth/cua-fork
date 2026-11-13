"""Start several cua-driver daemons one after the other, keep them alive, and report how each one's display captures go."""

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BINS = Path(os.environ.get("AB_BINS", "/tmp/cua-mem/bins"))
CALLS = int(os.environ.get("AB_CALLS", "5"))
FLAGS = ["--embedded"] + ([] if os.environ.get("AB_OVERLAY") == "on" else ["--no-overlay"])
ENV = {
    **os.environ,
    "CUA_DRIVER_EMBEDDED": "1",
    "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false",
    "CUA_DRIVER_RS_UPDATE_CHECK": "false",
}


def run(build: str, index: int) -> tuple[subprocess.Popen, Path]:
    binary = str(BINS / f"cua-driver-{build}")
    work = Path(tempfile.mkdtemp(prefix="cua-multi-"))
    sock = str(work / "d.sock")
    log = (work / "serve.log").open("w")
    daemon = subprocess.Popen(
        [binary, "serve", "--socket", sock, "--pid-file", str(work / "d.pid"), *FLAGS],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        env=ENV,
        start_new_session=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not Path(sock).exists():
        time.sleep(0.2)
    times, failed = [], 0
    for _ in range(CALLS):
        started = time.monotonic()
        try:
            done = subprocess.run(
                [binary, "call", "--socket", sock, "get_desktop_state", json.dumps({"screenshot_out_file": str(work / "shot.png")})],
                capture_output=True,
                text=True,
                timeout=120,
                env=ENV,
            )
            bad = done.returncode != 0 or '"isError": true' in done.stdout
        except subprocess.TimeoutExpired:
            bad = True
        times.append((time.monotonic() - started) * 1000)
        failed += bad
    window_ms = []
    listed = subprocess.run([binary, "call", "--socket", sock, "list_windows"], capture_output=True, text=True, timeout=60, env=ENV)
    windows = [w for w in json.loads(listed.stdout).get("windows", []) if w.get("is_on_screen") and w.get("bounds", {}).get("height", 0) > 300]
    if windows:
        target = max(windows, key=lambda w: w["bounds"]["width"] * w["bounds"]["height"])
        for _ in range(3):
            started = time.monotonic()
            subprocess.run(
                [binary, "call", "--socket", sock, "get_window_state", json.dumps({"pid": target["pid"], "window_id": target["window_id"], "screenshot_out_file": str(work / "window.png")})],
                capture_output=True,
                text=True,
                timeout=120,
                env=ENV,
            )
            window_ms.append(round((time.monotonic() - started) * 1000))
    text = (work / "serve.log").read_text()
    surfaces = subprocess.run(["vmmap", "-summary", str(daemon.pid)], capture_output=True, text=True).stdout
    surface_row = next((" ".join(line.split()[:2]) for line in surfaces.splitlines() if line.startswith("IOSurface")), "IOSurface none")
    status = daemon.poll()
    print(
        f"#{index} {build} pid={daemon.pid} first={times[0]:.0f} ms rest_p50={statistics.median(times[1:] or [0]):.0f} ms"
        f" failed={failed}/{CALLS} sck_timeouts={text.count('timed out after')} sck_blocked={text.count('still blocked')}"
        f" window_ms={window_ms} {surface_row} exit={'alive' if status is None else status}",
        flush=True,
    )
    return daemon, work


def main() -> None:
    daemons = []
    try:
        for index, build in enumerate(sys.argv[1:], 1):
            daemons.append(run(build, index))
        for index, (daemon, work) in enumerate(daemons, 1):
            status = daemon.poll()
            text = (work / "serve.log").read_text()
            print(f"end #{index} exit={'alive' if status is None else status} sck_timeouts={text.count('timed out after')}", flush=True)
    finally:
        for daemon, _ in daemons:
            if daemon.poll() is None:
                daemon.terminate()
        for daemon, _ in daemons:
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()


main()
