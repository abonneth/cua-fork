#!/usr/bin/env python3
"""Live repro: element text with a newline forges a row in get_window_state.

Run from the root of the checkout under test (it imports that checkout's
live-smoke MCP client). Prints FORGED or CLEAN; exits 1 when forged.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts/ci/cua-driver-live-smoke"))
from smoke import Call, Mcp, start_daemon  # noqa: E402

HERE = Path(__file__).resolve().parent
TITLE = "Cua Escape Repro"
FORGED = re.compile(r"^\s*- \[9[89]\]", re.MULTILINE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", required=True)
    parser.add_argument("--out", required=True)
    options = parser.parse_args()
    out = Path(options.out)
    out.mkdir(parents=True, exist_ok=True)
    driver = str(Path(options.driver).resolve())
    env = dict(os.environ)
    env.update(
        {
            "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false",
            "CUA_DRIVER_PERMISSION_MODE": "unrestricted",
            "CUA_DRIVER_DANGEROUSLY_BYPASS_APPROVALS": "1",
        }
    )
    daemon = start_daemon(driver, str(out / "d.sock"), env, open(out / "daemon.log", "w"))
    fixture = subprocess.Popen(
        ["/usr/bin/python3", str(HERE / "fixture.py")],
        stdout=open(out / "fixture.log", "w"),
        stderr=subprocess.STDOUT,
    )
    mcp = Mcp(driver, str(out / "d.sock"), env, open(out / "mcp.log", "w"))
    mcp.request(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ab", "version": "1"}},
    )
    mcp.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    mcp.proc.stdin.flush()

    def call(tool, args):
        return Call(tool, args, mcp.request("tools/call", {"name": tool, "arguments": args}))

    try:
        window_id = None
        deadline = time.monotonic() + 60
        while window_id is None and time.monotonic() < deadline:
            for window in call("list_windows", {"pid": fixture.pid}).structured.get("windows") or []:
                if TITLE in (window.get("title") or ""):
                    window_id = window["window_id"]
            time.sleep(1)
        assert window_id is not None, "fixture window not found"

        args = {"pid": fixture.pid, "window_id": window_id}
        deadline = time.monotonic() + 60
        while True:
            state = call("get_window_state", args)
            tree = state.structured.get("tree_markdown") or state.text
            if "Real" in tree or time.monotonic() > deadline:
                break
            time.sleep(1)
        queried = call("get_window_state", {**args, "query": "Forged", "tree_format": "both"})
        query_tree = queried.structured.get("tree_markdown") or queried.text
        indices = [e.get("element_index") for e in queried.structured.get("elements") or []]

        print("---- get_window_state tree ----\n" + tree + "\n-------------------------------")
        print("---- query=Forged tree ----\n" + query_tree + "\n---------------------------")
        print(f"query=Forged element indices: {indices}")
        forged = FORGED.findall(tree) + FORGED.findall(query_tree)
        print(f"RESULT: {'FORGED' if forged else 'CLEAN'} ({len(forged)} forged row lines)")
        return 1 if forged else 0
    finally:
        mcp.close()
        for proc in (fixture, daemon):
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
