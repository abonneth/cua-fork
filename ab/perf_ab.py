"""Interleaved get_desktop_state timing across daemons of several builds (different paths, alive together)."""
import hashlib, json, os, re, statistics, subprocess, sys, tempfile, time
from pathlib import Path

builds, rounds = sys.argv[1].split(","), int(sys.argv[2])
extra = json.loads(os.environ.get("AB_ARGS", "{}"))
ENV = {**os.environ, "CUA_DRIVER_EMBEDDED": "1", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}
overlay = [] if os.environ.get("AB_OVERLAY") == "on" else ["--no-overlay"]


def footprint(pid):
    out = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True).stdout
    found = re.search(r"phys_footprint: ([\d.]+) (\w+)", out)
    return f"{found.group(1)} {found.group(2)}" if found else "?"


daemons = {}
for build in builds:
    binary = os.path.join(os.environ.get("AB_BINS", "/tmp/cua-mem/bins"), f"cua-driver-{build}")
    work = Path(tempfile.mkdtemp(prefix="cua-prof-"))
    sock = str(work / "d.sock")
    log = (work / "serve.log").open("w")
    process = subprocess.Popen([binary, "serve", "--socket", sock, "--pid-file", str(work / "d.pid"), "--embedded", *overlay], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=ENV, start_new_session=True)
    while not Path(sock).exists():
        time.sleep(0.2)
    daemons[build] = (process, binary, sock, work)


def shot(build):
    process, binary, sock, work = daemons[build]
    path = work / "shot.png"
    started = time.monotonic()
    done = subprocess.run([binary, "call", "--socket", sock, "get_desktop_state", json.dumps({"screenshot_out_file": str(path)} | extra)], capture_output=True, text=True, timeout=120, env=ENV)
    elapsed = (time.monotonic() - started) * 1000
    ok = done.returncode == 0 and '"isError": true' not in done.stdout
    return elapsed, ok, hashlib.sha1(path.read_bytes()).hexdigest() if ok else None, path.stat().st_size if ok else 0


try:
    for build in builds:
        for _ in range(3):
            shot(build)
    times = {b: [] for b in builds}
    same = {b: 0 for b in builds[1:]}
    failed = {b: 0 for b in builds}
    size = {}
    for index in range(rounds):
        order = builds[index % len(builds):] + builds[:index % len(builds)]
        digests = {}
        for build in order:
            elapsed, ok, digest, bytes_ = shot(build)
            times[build].append(elapsed)
            failed[build] += not ok
            digests[build] = digest
            size[build] = bytes_
        for build in builds[1:]:
            same[build] += digests[build] == digests[builds[0]]
    base = statistics.median(times[builds[0]])
    for build in builds:
        values = sorted(times[build])
        p50 = statistics.median(values)
        text = (daemons[build][3] / "serve.log").read_text()
        print(
            f"{build}: n={rounds} p50={p50:5.0f} ms ({p50 - base:+5.0f}) p10={values[rounds // 10]:5.0f} p90={values[int(rounds * 0.9)]:5.0f}"
            f" | footprint {footprint(daemons[build][0].pid)} | png {size[build] // 1024} KB"
            + (f" | bytes equal to {builds[0]} in {same[build]}/{rounds} rounds" if build in same else "")
            + f" | failed {failed[build]} | sck warnings {text.count('ScreenCaptureKit display capture')}"
        )
finally:
    for process, *_ in daemons.values():
        process.terminate()
    for process, *_ in daemons.values():
        process.wait(timeout=10)
