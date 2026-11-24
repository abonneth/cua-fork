import socket
import sys
import time

path, count, hold = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
held, failed = [], 0
for _ in range(count):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(2)
    try:
        s.connect(path)
        held.append(s)
    except OSError:
        failed += 1
        s.close()
print(f"holder: opened {len(held)} connections, {failed} failed", flush=True)
time.sleep(hold)
for s in held:
    s.close()
print("holder: closed all connections", flush=True)
