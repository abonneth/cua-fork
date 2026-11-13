"""Find a round where two builds return the same pixels, then compare how each PNG was encoded."""
import collections, json, os, struct, subprocess, sys, tempfile, time, zlib
from pathlib import Path
from PIL import Image, ImageChops

BINS = Path(os.environ.get("AB_BINS", "/tmp/cua-mem/bins"))
ENV = {**os.environ, "CUA_DRIVER_EMBEDDED": "1", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false", "CUA_DRIVER_RS_UPDATE_CHECK": "false"}
work = Path(tempfile.mkdtemp(prefix="cua-prof-"))
args = json.loads(os.environ.get("AB_ARGS", "{}"))
builds = sys.argv[1].split(",")
socks, procs = {}, []


def shot(build):
    path = work / f"{build}.png"
    done = subprocess.run([str(BINS / f"cua-driver-{build}"), "call", "--socket", socks[build], "get_desktop_state", json.dumps({"screenshot_out_file": str(path), **args})], capture_output=True, text=True, timeout=60, env=ENV)
    assert done.returncode == 0, done.stdout + done.stderr
    return path.read_bytes()


def describe(build, raw):
    offset, idat = 8, b""
    while offset < len(raw):
        length, kind = struct.unpack_from(">I4s", raw, offset)
        if kind == b"IDAT":
            idat += raw[offset + 8 : offset + 8 + length]
        offset += 12 + length
    width, height = struct.unpack_from(">II", raw, 16)
    data = zlib.decompress(idat)
    stride = width * 4 + 1
    filters = collections.Counter(data[row * stride] for row in range(height))
    print(f"{build}: {len(raw)} bytes, zlib header {idat[:2].hex()}, row filters {dict(sorted(filters.items()))}")


try:
    for build in builds:
        socks[build] = str(work / f"{build}.sock")
        log = (work / f"{build}.log").open("w")
        procs.append(subprocess.Popen([str(BINS / f"cua-driver-{build}"), "serve", "--socket", socks[build], "--pid-file", str(work / f"{build}.pid"), "--embedded", "--no-overlay"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=ENV, start_new_session=True))
        while not Path(socks[build]).exists():
            time.sleep(0.2)
        shot(build)
    for attempt in range(1, 41):
        raws = {build: shot(build) for build in builds}
        images = [Image.open(work / f"{build}.png").copy() for build in builds]
        if images[0].size == images[1].size and ImageChops.difference(images[0], images[1]).getbbox(alpha_only=False) is None:
            print(f"attempt {attempt}: the same RGBA pixels, {images[0].size[0]}x{images[0].size[1]}; bytes equal: {raws[builds[0]] == raws[builds[1]]}")
            for build in builds:
                describe(build, raws[build])
            break
    else:
        print("no round with the same pixels in 40 attempts")
finally:
    for process in procs:
        process.terminate(); process.wait(timeout=10)
