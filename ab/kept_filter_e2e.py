"""A daemon that keeps its display filter: does a window that opens, moves and closes after the first capture show in later captures?"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from PIL import Image

build = sys.argv[1]
binary = f"/tmp/cua-mem/bins/cua-driver-{build}"
ENV = {**os.environ, "CUA_DRIVER_EMBEDDED": "1", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}
work = Path(tempfile.mkdtemp(prefix="cua-prof-"))
sock = str(work / "d.sock")
log = (work / "serve.log").open("w")
daemon = subprocess.Popen([binary, "serve", "--socket", sock, "--pid-file", str(work / "d.pid"), "--embedded", "--no-overlay"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=ENV, start_new_session=True)
while not Path(sock).exists():
    time.sleep(0.2)


def magenta_at(x_pt, y_pt):
    shot = work / "shot.png"
    done = subprocess.run([binary, "call", "--socket", sock, "get_desktop_state", json.dumps({"screenshot_out_file": str(shot), "max_image_dimension": 0})], capture_output=True, text=True, timeout=60, env=ENV)
    assert done.returncode == 0, done.stdout + done.stderr
    image = Image.open(shot).convert("RGB")
    scale = image.width / 1710
    r, g, b = image.getpixel((int((x_pt + 100) * scale), int((y_pt + 100) * scale)))
    return r > 200 and g < 80 and b > 200


def window(x, y, hold):
    process = subprocess.Popen(["/tmp/cua-mem/magenta_win", str(x), str(y), str(hold)], stdout=subprocess.PIPE, text=True)
    process.stdout.readline()
    time.sleep(0.7)
    return process


try:
    for _ in range(3):
        magenta_at(300, 300)
    print(f"{build} before any window: magenta at (300,300)={magenta_at(300, 300)}")
    first = window(300, 300, 60)
    print(f"{build} window opened after 4 captures: magenta at (300,300)={magenta_at(300, 300)}")
    first.kill(); first.wait(); time.sleep(0.7)
    print(f"{build} window closed: magenta at (300,300)={magenta_at(300, 300)}")
    second = window(800, 500, 60)
    print(f"{build} another window opened elsewhere: magenta at (800,500)={magenta_at(800, 500)} at (300,300)={magenta_at(300, 300)}")
    second.kill(); second.wait(); time.sleep(0.7)
    print(f"{build} closed again: magenta at (800,500)={magenta_at(800, 500)}")
    text = (work / "serve.log").read_text()
    print(f"{build} display capture warnings in the daemon log: {text.count('ScreenCaptureKit display capture')}")
finally:
    daemon.terminate(); daemon.wait(timeout=10)
