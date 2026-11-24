"""Signal the pid of a relaunching macOS `cua-driver serve` and check the relaunched daemon."""

import os
import signal
import subprocess
import sys
import tempfile
import time

BIN = os.path.realpath(sys.argv[1])
env = dict(os.environ, CUA_DRIVER_RS_TELEMETRY_ENABLED="false")
env.pop("CUA_DRIVER_RS_RESPONSIBILITY_DISCLAIMED", None)
SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def default_signals():
    for sig in SIGNALS:
        signal.signal(sig, signal.SIG_DFL)


def answers(sock):
    return subprocess.run([BIN, "status", "--socket", sock], capture_output=True).returncode == 0


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


failures = 0
for sig in SIGNALS:
    name = sig.name
    d = tempfile.mkdtemp(prefix="cua-l-", dir="/tmp")
    sock, pid_file = f"{d}/d.sock", f"{d}/d.pid"
    log = open(f"{d}/serve.log", "w")
    launcher = subprocess.Popen(
        [BIN, "serve", "--socket", sock, "--pid-file", pid_file, "--no-overlay", "--no-permissions-gate"],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, preexec_fn=default_signals,
    )
    deadline = time.monotonic() + 30
    while not answers(sock):
        assert launcher.poll() is None and time.monotonic() < deadline, open(f"{d}/serve.log").read()
        time.sleep(0.1)
    daemon = int(open(pid_file).read().strip())
    print(f"{name}: launcher pid {launcher.pid}, daemon pid {daemon}", flush=True)
    os.kill(launcher.pid, sig)
    try:
        code = launcher.wait(15)
        status = f"signal {-code}" if code < 0 else f"exit {code}"
    except subprocess.TimeoutExpired:
        status = "still running after 15 s"
    time.sleep(1)
    daemon_alive = alive(daemon)
    print(
        f"{name}: launcher {status}; daemon {'STILL RUNNING' if daemon_alive else 'stopped'}; "
        f"socket {'LEFT' if os.path.exists(sock) else 'removed'}; "
        f"pid file {'LEFT' if os.path.exists(pid_file) else 'removed'}",
        flush=True,
    )
    failures += daemon_alive or os.path.exists(sock) or os.path.exists(pid_file)
    if daemon_alive:
        os.kill(daemon, signal.SIGKILL)
    if launcher.poll() is None:
        launcher.kill()
        launcher.wait()
    log.close()
sys.exit(1 if failures else 0)
