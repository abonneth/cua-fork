import socket
import sys
import time

target, count, hold = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
held, failed = [], 0
for _ in range(count):
    if target.startswith("tcp:"):
        s = socket.socket(socket.AF_INET)
        address = ("127.0.0.1", int(target[4:]))
    else:
        s = socket.socket(socket.AF_UNIX)
        address = target
    s.settimeout(2)
    try:
        s.connect(address)
        held.append(s)
    except OSError:
        failed += 1
        s.close()
print(f"holder: opened {len(held)} connections, {failed} failed", flush=True)
time.sleep(hold)
for s in held:
    s.close()
print("holder: closed all connections", flush=True)
