"""Desktop screenshots of two builds taken back to back: how many pixels differ, where, and by how much."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from PIL import Image, ImageChops

BINS = Path(os.environ.get("AB_BINS", "/tmp/cua-mem/bins"))
ENV = {**os.environ, "CUA_DRIVER_EMBEDDED": "1", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}
work = Path(tempfile.mkdtemp(prefix="cua-prof-"))
builds = sys.argv[1].split(",")
daemons = {}
for build in builds:
    sock = str(work / f"{build}.sock")
    log = (work / f"{build}.log").open("w")
    daemons[build] = (subprocess.Popen([str(BINS / f"cua-driver-{build}"), "serve", "--socket", sock, "--pid-file", str(work / f"{build}.pid"), "--embedded", "--no-overlay"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=ENV, start_new_session=True), sock)
    while not Path(sock).exists():
        time.sleep(0.2)


def shot(build, name):
    path = work / f"{name}.png"
    done = subprocess.run([str(BINS / f"cua-driver-{build}"), "call", "--socket", daemons[build][1], "get_desktop_state", json.dumps({"screenshot_out_file": str(path), "max_image_dimension": 0})], capture_output=True, text=True, timeout=60, env=ENV)
    assert done.returncode == 0, done.stdout + done.stderr
    return Image.open(path).convert("RGB")


def compare(label, left, right):
    if left.size != right.size:
        print(f"{label}: sizes differ {left.size} vs {right.size}")
        return
    diff = ImageChops.difference(left, right)
    box = diff.getbbox()
    if box is None:
        print(f"{label}: {left.size[0]}x{left.size[1]}, 0 pixels differ")
        return
    mask = diff.convert("L").point(lambda value: 255 if value else 0)
    differing = sum(1 for value in mask.getdata() if value)
    extrema = [channel[1] for channel in diff.getextrema()]
    print(f"{label}: {left.size[0]}x{left.size[1]}, {differing} pixels differ, inside box {box}, largest channel difference {max(extrema)}")


try:
    for build in builds:
        shot(build, "warm")
    first, second = builds[0], builds[1]
    for round_number in range(1, 4):
        a = shot(first, "a")
        b = shot(second, "b")
        c = shot(first, "c")
        compare(f"round {round_number} {first} then {second}", a, b)
        compare(f"round {round_number} {first} then {first} (control)", a, c)
finally:
    for process, _ in daemons.values():
        process.terminate()
        process.wait(timeout=10)
