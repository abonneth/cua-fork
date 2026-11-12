"""Count the pixels that differ between screenshots, pair by pair."""

import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def pixels(png: Path) -> tuple[int, int, bytes]:
    bmp = Path(tempfile.mkdtemp()) / "image.bmp"
    subprocess.run(["sips", "-s", "format", "bmp", str(png), "--out", str(bmp)], capture_output=True, check=True)
    raw = bmp.read_bytes()
    offset = struct.unpack_from("<I", raw, 10)[0]
    width, height = struct.unpack_from("<ii", raw, 18)
    return width, abs(height), raw[offset:]


folder = Path(sys.argv[1])
for pair in sys.argv[2:]:
    left, right = (folder / f"{name}.png" for name in pair.split(":"))
    if not (left.exists() and right.exists()):
        print(f"{pair}: missing file")
        continue
    (lw, lh, a), (rw, rh, b) = pixels(left), pixels(right)
    if (lw, lh) != (rw, rh) or len(a) != len(b):
        print(f"{pair}: sizes differ {lw}x{lh} vs {rw}x{rh}")
        continue
    step = len(a) // (lw * lh)
    differing = sum(1 for i in range(0, len(a), step) if a[i : i + step] != b[i : i + step])
    print(f"{pair}: {lw}x{lh}, {differing} of {lw * lh} pixels differ ({100 * differing / (lw * lh):.3f}%)")
