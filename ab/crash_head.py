"""Print the exception and the crashing thread's top frames of a macOS .ips crash report."""

import json
import sys

head, body = open(sys.argv[1]).read().split("\n", 1)
head, body = json.loads(head), json.loads(body)
print(sys.argv[1])
print(head.get("app_name"), head.get("app_version"), body.get("exception"), body.get("termination", {}).get("indicator"))
images = body["usedImages"]
thread = next(t for t in body["threads"] if t.get("triggered"))
for frame in thread["frames"][:16]:
    print("  ", images[frame["imageIndex"]].get("name", "?"), frame.get("symbol", hex(frame.get("imageOffset", 0))))
