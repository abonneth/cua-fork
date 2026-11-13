"""Two ScreenCaptureKit clients alive together: does each get an image, and what does replayd burn."""
import re, subprocess, sys, time

def replayd_cpu(seconds=4):
    out = subprocess.run(["top", "-l", "2", "-s", str(seconds), "-stats", "pid,command,cpu"], capture_output=True, text=True).stdout
    rows = re.findall(r"^\d+\s+replayd\s+([\d.]+)", out, re.M)
    return float(rows[-1]) if rows else -1.0

def settle():
    subprocess.run(["pkill", "-9", "-f", "two_clients"])
    for _ in range(10):
        if replayd_cpu(2) < 2: return
    print("replayd did not settle")

def case(label, first, second, gap=4.0):
    settle()
    outs = []
    procs = []
    for exe, wait in ((first, gap), (second, 7.0)):
        procs.append(subprocess.Popen([exe, "filter", "60"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
        time.sleep(wait)
    cpu = replayd_cpu(5)
    for p in procs: p.kill()
    for p in procs:
        text = p.communicate()[0].strip()
        outs.append(re.sub(r"pid \d+ filter: ", "", text) or "no answer in 12 s+")
    print(f"{label:34s} replayd {cpu:5.1f}% | first: {outs[0]} | second: {outs[1]}", flush=True)

B1 = "bundles/one/Probe.app/Contents/MacOS/two_clients"
B2 = "bundles/two/Probe.app/Contents/MacOS/two_clients"
NESTED = "bundles/one/Probe.app/Contents/Resources/inner/bin/two_clients"
HELPER = "bundles/one/Probe.app/Contents/Helpers/two_clients"
for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1):
    case("same path, no bundle", "./two_clients", "./two_clients")
    case("same path, embedded __info_plist", "./two_clients_plist", "./two_clients_plist")
    case("same path, in .app/Contents/Resources", NESTED, NESTED)
    case("same path, in .app/Contents/Helpers", HELPER, HELPER)
    case("two paths, no bundle", "./two_clients", "./two_clients_copy")
    case("two paths, same bundle id", B1, B2)
    case("same path, .app/Contents/MacOS", B1, B1)
settle()
