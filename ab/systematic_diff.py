"""Pixels where build B differs from build A while A's two bracketing captures agree, counted over rounds: a difference of the route, not of the screen."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
import numpy as np
from PIL import Image

BINS = Path(os.environ.get("AB_BINS", "/tmp/cua-mem/bins"))
ENV = {**os.environ, "CUA_DRIVER_EMBEDDED": "1", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}
work = Path(tempfile.mkdtemp(prefix="cua-prof-"))
args = json.loads(os.environ.get("AB_ARGS", "{}"))
first, second = sys.argv[1].split(",")
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 12
socks, procs = {}, []


def shot(build):
    path = work / f"{build}.png"
    done = subprocess.run([str(BINS / f"cua-driver-{build}"), "call", "--socket", socks[build], "get_desktop_state", json.dumps({"screenshot_out_file": str(path), **args})], capture_output=True, text=True, timeout=60, env=ENV)
    assert done.returncode == 0, done.stdout + done.stderr
    return np.asarray(Image.open(path).convert("RGBA")).copy()


try:
    for build in (first, second):
        socks[build] = str(work / f"{build}.sock")
        log = (work / f"{build}.log").open("w")
        procs.append(subprocess.Popen([str(BINS / f"cua-driver-{build}"), "serve", "--socket", socks[build], "--pid-file", str(work / f"{build}.pid"), "--embedded", "--no-overlay"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=ENV, start_new_session=True))
        while not Path(socks[build]).exists():
            time.sleep(0.2)
        shot(build)
    stable_rounds = differs_rounds = None
    whole = 0
    for _ in range(rounds):
        a, b, c = shot(first), shot(second), shot(first)
        stable = (a == c).all(axis=2)
        differs = stable & (a != b).any(axis=2)
        whole += int(not (a != b).any())
        stable_rounds = stable.astype(np.int32) if stable_rounds is None else stable_rounds + stable
        differs_rounds = differs.astype(np.int32) if differs_rounds is None else differs_rounds + differs
        time.sleep(0.4)
    height, width = stable_rounds.shape
    always = (stable_rounds >= rounds // 2) & (differs_rounds == stable_rounds)
    print(f"{first} vs {second}, {width}x{height}, {rounds} rounds: whole image identical in {whole} rounds; "
          f"{int((stable_rounds >= rounds // 2).sum())} of {width * height} pixels held still in at least {rounds // 2} rounds; "
          f"of those, {int(always.sum())} differ between the builds every time, {int(((differs_rounds > 0) & (stable_rounds >= rounds // 2)).sum())} at least once")
    if always.any():
        ys, xs = np.nonzero(always)
        print(f"  those pixels sit in box ({xs.min()}, {ys.min()}, {xs.max() + 1}, {ys.max() + 1})")
finally:
    for process in procs:
        process.terminate(); process.wait(timeout=10)
