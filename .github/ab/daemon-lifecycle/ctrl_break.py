"""Send Ctrl+Break to a Windows `cua-driver serve` and report what it left behind."""

import os
import signal
import subprocess
import sys
import tempfile
import time

binary = sys.argv[1]
work = tempfile.mkdtemp()
pid_file = os.path.join(work, "d.pid")
log_path = os.path.join(work, "serve.log")
pipe = rf"\\.\pipe\cua-ab-ctrl-break-{os.getpid()}"
env = dict(os.environ, CUA_DRIVER_RS_TELEMETRY_ENABLED="false", CUA_TELEMETRY="0")


def answers() -> bool:
    return (
        subprocess.run(
            [binary, "status", "--socket", pipe],
            capture_output=True,
            env=env,
        ).returncode
        == 0
    )


with open(log_path, "wb") as log:
    daemon = subprocess.Popen(
        [binary, "serve", "--socket", pipe, "--pid-file", pid_file,
         "--no-overlay", "--no-permissions-gate"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    deadline = time.time() + 60
    while not answers():
        if daemon.poll() is not None or time.time() > deadline:
            print("daemon never became ready")
            sys.exit(2)
        time.sleep(0.2)
    print(f"daemon pid {daemon.pid} ready; pid file holds {open(pid_file).read().strip()}")
    daemon.send_signal(signal.CTRL_BREAK_EVENT)
    try:
        code = daemon.wait(30)
    except subprocess.TimeoutExpired:
        daemon.kill()
        code = None

print(f"daemon exit code: {code if code is None else hex(code & 0xFFFFFFFF)}")
left = os.path.exists(pid_file)
print(f"pid file: {'LEFT' if left else 'removed'}")
print("--- serve.log (last 5 lines)")
print("\n".join(open(log_path, errors="replace").read().splitlines()[-5:]))
sys.exit(0 if code == 0 and not left else 1)
