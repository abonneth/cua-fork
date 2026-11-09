"""A/B: does a detached `cua-driver serve` outlive the host that started it, once that host is killed?"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

REPS = 5
GRACE_S = 20
WINDOWS = sys.platform == "win32"
BINS = {"A": sys.argv[1], "B": sys.argv[2]}
ENV = {**os.environ, "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}
EXTRA = ["--no-permissions-gate"] if sys.platform == "darwin" else []


def endpoint(variant: str, index: int) -> str:
    if WINDOWS:
        return rf"\\.\pipe\ab-owner-{variant}-{index}"
    return os.path.join(tempfile.gettempdir(), f"ab-owner-{variant}-{index}.sock")


def ping(path: str) -> bool:
    try:
        if WINDOWS:
            with open(path, "r+b", buffering=0) as pipe:
                pipe.write(b'{"method":"list"}\n')
                return bool(pipe.read(16))
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(path)
            client.sendall(b'{"method":"list"}\n')
            return bool(client.recv(16))
    except OSError:
        return False


def detached() -> dict:
    if WINDOWS:
        return {"creationflags": 0x00000008 | 0x00000200}
    return {"start_new_session": True}


def rep(variant: str, index: int) -> dict:
    exe = os.path.join(BINS[variant], "cua-driver.exe" if WINDOWS else "cua-driver")
    path = endpoint(variant, index)
    pid_file = os.path.join(tempfile.gettempdir(), f"ab-owner-{variant}-{index}.pid")
    host = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], **detached())
    owner = ["--owner-pid", str(host.pid)] if variant == "B" else []
    daemon = subprocess.Popen(
        [exe, "serve", "--socket", path, "--pid-file", pid_file, *owner, *EXTRA],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=ENV,
        **detached(),
    )
    deadline = time.monotonic() + 60
    while not ping(path):
        if daemon.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(f"{variant}#{index} daemon never served (exit {daemon.poll()})")
        time.sleep(0.2)
    time.sleep(3)
    alive_with_host = daemon.poll() is None and ping(path)
    host.kill()
    host.wait()
    killed = time.monotonic()
    try:
        daemon.wait(GRACE_S)
        exited_after = round(time.monotonic() - killed, 3)
    except subprocess.TimeoutExpired:
        exited_after = None
    result = {
        "alive_with_host": alive_with_host,
        "exited_after_s": exited_after,
        "exit_code": daemon.returncode,
        "endpoint_answers": ping(path),
        "pid_file_left": os.path.exists(pid_file),
    }
    if daemon.poll() is None:
        daemon.kill()
        daemon.wait()
    return result


rows = {v: [] for v in BINS}
for index in range(REPS):
    for variant in BINS:
        outcome = rep(variant, index)
        rows[variant].append(outcome)
        print(f"rep {index} {variant}: {outcome}")

lines = [
    f"{sys.platform}, {REPS} reps per arm, host killed after 3 s",
    "",
    "| | A: upstream | B: --owner-pid |",
    "|---|---|---|",
]
exits = lambda rs: [r["exited_after_s"] for r in rs if r["exited_after_s"] is not None]
lines += [
    f"| alive while the host runs | {sum(r['alive_with_host'] for r in rows['A'])}/{REPS} | {sum(r['alive_with_host'] for r in rows['B'])}/{REPS} |",
    f"| exited within {GRACE_S} s of the kill | {len(exits(rows['A']))}/{REPS} | {len(exits(rows['B']))}/{REPS} |",
    "| exit latency (max) | "
    + " | ".join(f"{max(exits(rows[v])):.2f} s" if exits(rows[v]) else "-" for v in BINS)
    + " |",
    f"| endpoint still answering | {sum(r['endpoint_answers'] for r in rows['A'])}/{REPS} | {sum(r['endpoint_answers'] for r in rows['B'])}/{REPS} |",
    f"| pid file left behind | {sum(r['pid_file_left'] for r in rows['A'])}/{REPS} | {sum(r['pid_file_left'] for r in rows['B'])}/{REPS} |",
]
summary = "\n".join(lines)
print(summary)
with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as out:
    out.write(summary + "\n\n")
print("RESULTS_JSON " + json.dumps(rows))
