"""Memory of a cua-driver daemon across display and window captures: footprint, live CGImage and IOSurface objects, call time."""

import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BIN, LABEL = sys.argv[1], sys.argv[2]
OVERLAY = os.environ.get("AB_OVERLAY", "off") == "on"
N = int(os.environ.get("AB_CALLS", "30"))
WORK = Path(tempfile.mkdtemp(prefix="cua-mem-"))
SOCK = str(WORK / "d.sock")
ENV = {
    **os.environ,
    "CUA_DRIVER_EMBEDDED": "1",
    "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false",
    "CUA_DRIVER_RS_UPDATE_CHECK": "false",
}


def call(tool: str, args: dict | None = None) -> tuple[bool, str, float]:
    command = [BIN, "call", "--socket", SOCK, tool]
    if args is not None:
        command.append(json.dumps(args))
    started = time.monotonic()
    done = subprocess.run(command, capture_output=True, text=True, timeout=180, env=ENV)
    elapsed = (time.monotonic() - started) * 1000
    text = done.stdout + done.stderr
    failed = done.returncode != 0 or '"isError": true' in done.stdout or "failed" in done.stderr
    return not failed, text, elapsed


def footprint(pid: int) -> tuple[float, str]:
    out = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True).stdout
    total = re.search(r"phys_footprint: ([\d.]+) (\w+)", out)
    scale = {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0, "B": 1 / 1048576}
    megabytes = float(total.group(1)) * scale[total.group(2)] if total else -1.0
    large = re.search(r"^\s*([\d.]+ \w+)\s+\S+ \w+\s+\S+ \w+\s+(\d+)\s+MALLOC_LARGE", out, re.M)
    return megabytes, f"{large.group(1)} in {large.group(2)}" if large else "none"


def surfaces(pid: int) -> str:
    out = subprocess.run(["vmmap", "-summary", str(pid)], capture_output=True, text=True).stdout
    row = re.search(r"^IOSurface\s+([\d.]+[KMG])\s.*?(\d+)\s*$", out, re.M)
    return f"{row.group(1)} in {row.group(2)}" if row else "none"


def stuck(pid: int) -> str:
    """Large blocks CoreGraphics holds in its purgeable zone that the kernel cannot purge."""
    out = subprocess.run(["vmmap", str(pid)], capture_output=True, text=True).stdout
    sizes = []
    for line in out.splitlines():
        if line.startswith("MALLOC_LARGE ") and "DefaultPurgeableMallocZone" in line and "PURGE=" not in line:
            found = re.search(r"\[\s*([\d.]+)([KMG])", line)
            if found:
                sizes.append(float(found.group(1)) * {"K": 1 / 1024, "M": 1.0, "G": 1024.0}[found.group(2)])
    return f"{len(sizes)} ({sum(sizes):.0f} MB)"


def live(pid: int) -> dict[str, int]:
    out = subprocess.run(["heap", str(pid)], capture_output=True, text=True).stdout
    counts = {}
    for name in ("CGImage", "IOSurface"):
        found = re.search(rf"^\s*(\d+)\s+\d+\s+[\d.]+\s+{name}\s", out, re.M)
        counts[name] = int(found.group(1)) if found else 0
    return counts


def report(pid: int, label: str, times: list[float] | None = None) -> dict:
    megabytes, large = footprint(pid)
    counts = live(pid)
    timing = ""
    if times:
        ordered = sorted(times)
        timing = f" call_ms p50={statistics.median(ordered):.0f} p90={ordered[int(len(ordered) * 0.9) - 1]:.0f}"
    print(
        f"[{LABEL}] {label:44s} footprint={megabytes:8.1f} MB malloc_large={large:18s}"
        f" CGImage={counts['CGImage']:3d} IOSurface={counts['IOSurface']:3d}"
        f" iosurface_mapped={surfaces(pid):14s} unpurgeable_cg_copies={stuck(pid):14s}{timing}",
        flush=True,
    )
    return {"label": label, "footprint_mb": megabytes, **counts, "times_ms": times or []}


def phase(pid: int, label: str, tool: str, args: dict, count: int) -> dict:
    times, failures, first = [], 0, ""
    for _ in range(count):
        ok, text, elapsed = call(tool, args)
        if ok:
            times.append(elapsed)
        else:
            failures += 1
            first = first or text[:400].replace("\n", " ")
    time.sleep(3)
    row = report(pid, f"{count}x {label} (failed {failures})", times)
    if first:
        print(f"[{LABEL}]    first failure: {first}", flush=True)
    row["failures"] = failures
    return row


def target_window() -> tuple[int, int, str] | None:
    app = os.environ.get("AB_WINDOW_APP")
    if not app:
        app = "TextEdit"
        note = WORK / "note.txt"
        note.write_text("memory probe\n" * 40)
        subprocess.run(["open", "-a", app, str(note)], check=False)
    deadline = time.monotonic() + 30
    windows = []
    while time.monotonic() < deadline:
        ok, text, _ = call("list_windows")
        windows = json.loads(text).get("windows", []) if ok else []
        mine = [w for w in windows if w.get("app_name") == app and w.get("is_on_screen") and w.get("bounds", {}).get("height", 0) > 100]
        if mine:
            best = max(mine, key=lambda w: w["bounds"]["width"] * w["bounds"]["height"])
            bounds = best["bounds"]
            return best["pid"], best["window_id"], f"{app} {bounds['width']:.0f}x{bounds['height']:.0f} pt"
        time.sleep(1)
    listed = [(w.get("app_name"), w.get("is_on_screen"), w.get("bounds")) for w in windows]
    print(f"[{LABEL}] windows listed: {listed}", flush=True)
    return None


def main() -> int:
    flags = ["--embedded"] + ([] if OVERLAY else ["--no-overlay"])
    log = (WORK / "serve.log").open("w")
    daemon = subprocess.Popen(
        [BIN, "serve", "--socket", SOCK, "--pid-file", str(WORK / "d.pid"), *flags],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        env=ENV,
        start_new_session=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not Path(SOCK).exists():
        time.sleep(0.2)
    try:
        print(f"[{LABEL}] daemon pid {daemon.pid} flags {' '.join(flags)}", flush=True)
        ok, text, _ = call("check_permissions")
        print(f"[{LABEL}] permissions: {' '.join(text.split())[:300]}", flush=True)
        shot = str(WORK / "shot.png")
        rows = [report(daemon.pid, "start")]
        ok, text, elapsed = call("get_desktop_state", {"screenshot_out_file": shot})
        print(f"[{LABEL}] first get_desktop_state ok={ok} {elapsed:.0f} ms {' '.join(text.split())[:260]}", flush=True)
        if Path(shot).exists():
            size = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", shot], capture_output=True, text=True)
            print(f"[{LABEL}] screenshot {' '.join(size.stdout.split()[1:])}", flush=True)
        rows.append(report(daemon.pid, "after the first get_desktop_state"))
        for _ in range(2):
            rows.append(phase(daemon.pid, "get_desktop_state", "get_desktop_state", {"screenshot_out_file": shot}, N))
        time.sleep(20)
        rows.append(report(daemon.pid, "idle 20 s"))
        target = target_window()
        if target is None:
            print(f"[{LABEL}] no window to capture", flush=True)
        else:
            args = {"pid": target[0], "window_id": target[1], "screenshot_out_file": str(WORK / "window.png")}
            rows.append(phase(daemon.pid, f"get_window_state {target[2]}", "get_window_state", args, N))
            rows.append(phase(daemon.pid, "get_desktop_state", "get_desktop_state", {"screenshot_out_file": shot}, 5))
        keep = os.environ.get("AB_SHOTS")
        if keep:
            Path(keep).mkdir(parents=True, exist_ok=True)
            for name in ("shot.png", "window.png"):
                if (WORK / name).exists():
                    (Path(keep) / f"{LABEL}-{name}").write_bytes((WORK / name).read_bytes())
        alive = daemon.poll() is None
        print(f"[{LABEL}] daemon alive at the end: {alive}", flush=True)
        desktop = [row for row in rows if row["label"].startswith(f"{N}x get_desktop_state")]
        peak = max(row["footprint_mb"] for row in rows)
        summary = {
            "label": LABEL,
            "overlay": OVERLAY,
            "calls": 2 * N,
            "failures": sum(row["failures"] for row in desktop),
            "cgimage_before": rows[1]["CGImage"],
            "cgimage_after": desktop[-1]["CGImage"],
            "footprint_before_mb": rows[1]["footprint_mb"],
            "footprint_after_mb": desktop[-1]["footprint_mb"],
            "footprint_end_mb": rows[-1]["footprint_mb"],
            "footprint_peak_mb": peak,
            "unpurgeable_cg_copies_end": stuck(daemon.pid),
            "p50_ms": statistics.median([t for row in desktop for t in row["times_ms"]] or [0]),
        }
        print(f"[{LABEL}] SUMMARY {json.dumps(summary)}", flush=True)
    finally:
        if os.environ.get("AB_LEAVE") == "1" and daemon.poll() is None:
            print(f"[{LABEL}] daemon {daemon.pid} left running", flush=True)
        else:
            daemon.terminate()
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()
            print(f"[{LABEL}] daemon exit status {daemon.returncode}", flush=True)
        print((WORK / "serve.log").read_text()[-1500:], flush=True)
    return 0


sys.exit(main())
