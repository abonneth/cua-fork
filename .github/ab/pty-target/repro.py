#!/usr/bin/env python3
"""Terminal pty injection target repro: run under xvfb-run + dbus-run-session."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def reader(label, out, spawn_child):
    """Stand-in shell: logs its tty and every line it reads from it."""
    if spawn_child:
        subprocess.Popen(["sleep", "1000"])

    def log(**fields):
        with open(out, "a") as stream:
            stream.write(json.dumps(dict(label=label, **fields)) + "\n")

    log(event="ready", tty=os.ttyname(0), pid=os.getpid())
    for line in sys.stdin:
        log(event="line", text=line.rstrip("\n"))


def main(driver):
    work = Path(tempfile.mkdtemp(prefix="pty-target-"))
    out = work / "lines.jsonl"
    endpoint = str(work / "daemon.sock")
    procs = []
    failures = []

    def spawn(argv, **kw):
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, **kw)
        procs.append(p)
        return p

    def events(label=None, kind="line"):
        if not out.exists():
            return []
        rows = [json.loads(l) for l in out.read_text().splitlines()]
        return [r for r in rows if r["event"] == kind and (label is None or r["label"] == label)]

    def wait_for(pred, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.1)
        return False

    def call(tool, params):
        r = subprocess.run([driver, "--socket", endpoint, "call", tool, json.dumps(params)],
                           capture_output=True, text=True, timeout=30)
        body = r.stdout.strip() or r.stderr.strip()
        print(f"call {tool} {json.dumps(params)} -> rc={r.returncode} {body[:300]}", flush=True)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return {"raw": body}

    def window(title):
        found = []

        def look():
            nonlocal found
            found = [w for w in call("list_windows", {}).get("windows", [])
                     if title in (w.get("title") or "")]
            return bool(found)

        assert wait_for(look), f"no window titled {title}"
        return found[0]

    def check(name, ok, detail):
        print(f"RESULT {name}: {'PASS' if ok else 'FAIL'} ({detail})", flush=True)
        if not ok:
            failures.append(name)

    me = os.path.abspath(__file__)
    try:
        print("ptrace_scope", Path("/proc/sys/kernel/yama/ptrace_scope").read_text().strip())
        spawn(["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        daemon_log = (work / "daemon.log").open("w")
        spawn([driver, "serve", "--socket", endpoint, "--dangerously-bypass-approvals"],
              stdout=daemon_log, stderr=daemon_log)

        def daemon_up():
            try:
                with socket.socket(socket.AF_UNIX) as c:
                    c.connect(endpoint)
                return True
            except OSError:
                return False

        assert wait_for(daemon_up), "daemon did not start"

        # One xfce4-terminal process, two windows. Window A's reader has a child on the same pts.
        spawn(["xfce4-terminal", "--title=PTY-A", "-x", sys.executable, me, "--reader", "A", str(out), "child"])
        assert wait_for(lambda: events("A", "ready")), "reader A not ready"
        time.sleep(1)
        spawn(["xfce4-terminal", "--title=PTY-B", "-x", sys.executable, me, "--reader", "B", str(out), "-"])
        assert wait_for(lambda: events("B", "ready")), "reader B not ready"
        win_a, win_b = window("PTY-A"), window("PTY-B")
        ready = {r["label"]: r for r in events(kind="ready")}
        print(f"window A xid={win_a['window_id']} pid={win_a['pid']} tty={ready['A']['tty']}")
        print(f"window B xid={win_b['window_id']} pid={win_b['pid']} tty={ready['B']['tty']}")
        print(subprocess.run(["ps", "-o", "pid,ppid,tty,args", "--ppid", str(win_a["pid"])],
                             capture_output=True, text=True).stdout)
        assert win_a["pid"] == win_b["pid"], "expected one emulator process for both windows"

        target_b = {"pid": win_b["pid"], "window_id": win_b["window_id"], "delivery_mode": "foreground"}
        call("type_text", dict(target_b, text="to-B"))
        call("press_key", dict(target_b, key="Return"))
        wait_for(lambda: events("A") or events("B"), timeout=5)
        time.sleep(1)
        a_lines = [r["text"] for r in events("A")]
        b_lines = [r["text"] for r in events("B")]
        check("two-window foreground: input for B lands in B", b_lines == ["to-B"] and not a_lines,
              f"A got {a_lines}, B got {b_lines}")

        background_b = dict(target_b, delivery_mode="background")
        typed_bg = call("type_text", dict(background_b, text="bg-B\n"))
        time.sleep(2)
        a_after = [r["text"] for r in events("A")]
        check("two-window background: input for B never lands in A", a_after == a_lines,
              f"A got {a_after}, type_text code={typed_bg.get('code')}")

        # Single-window terminal keeps the pty route.
        spawn(["xterm", "-T", "PTY-C", "-e", sys.executable, me, "--reader", "C", str(out), "child"])
        assert wait_for(lambda: events("C", "ready")), "reader C not ready"
        win_c = window("PTY-C")
        target_c = {"pid": win_c["pid"], "window_id": win_c["window_id"]}
        typed = call("type_text", dict(target_c, text="to-C"))
        call("press_key", dict(target_c, key="Return"))
        wait_for(lambda: events("C"), timeout=5)
        c_lines = [r["text"] for r in events("C")]
        # Background key routes are refused here (see the B background case), so this is the pty route.
        mode = typed.get("delivery", {}).get("mode")
        check("single-window: background pty route delivers", c_lines == ["to-C"] and mode == "background",
              f"C got {c_lines}, delivery={mode}")

        # Enter through the pty must be Return (CR), which fzf accepts; LF is Ctrl-J (down).
        fzf_out = work / "fzf.out"
        spawn(["xterm", "-T", "PTY-FZF", "-e", "sh", "-c",
               f"printf 'one\\ntwo\\n' | fzf > {fzf_out}; sleep 1000"])
        win_f = window("PTY-FZF")
        time.sleep(3)
        call("press_key", {"pid": win_f["pid"], "window_id": win_f["window_id"], "key": "Return"})
        wait_for(lambda: fzf_out.exists() and fzf_out.read_text(), timeout=5)
        picked = fzf_out.read_text().strip() if fzf_out.exists() else ""
        check("single-window: Enter accepts in fzf", picked == "one", f"fzf output {picked!r}")

        fzf_typed = work / "fzf-typed.out"
        spawn(["xterm", "-T", "PTY-FZF-TYPED", "-e", "sh", "-c",
               f"printf 'one\\ntwo\\n' | fzf > {fzf_typed}; sleep 1000"])
        win_t = window("PTY-FZF-TYPED")
        time.sleep(3)
        call("type_text", {"pid": win_t["pid"], "window_id": win_t["window_id"], "text": "\n"})
        wait_for(lambda: fzf_typed.exists() and fzf_typed.read_text(), timeout=5)
        picked = fzf_typed.read_text().strip() if fzf_typed.exists() else ""
        check("single-window: typed newline accepts in fzf", picked == "one", f"fzf output {picked!r}")
    finally:
        subprocess.run([driver, "--socket", endpoint, "stop"], capture_output=True, timeout=20)
        for p in reversed(procs):
            p.kill()
        subprocess.run(["pkill", "-f", "sleep 1000"])
        subprocess.run(["pkill", "xfce4-terminal"])
    print("SUMMARY", "FAIL " + ", ".join(failures) if failures else "all PASS", flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--reader":
        reader(sys.argv[2], sys.argv[3], sys.argv[4] == "child")
    else:
        main(os.path.abspath(sys.argv[1]))
