#!/usr/bin/env bash
# SIGTERM a daemon that is recording video, then look at what it left behind.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/common.sh"
BIN=$(realpath "$1")

dir=$(mktemp -d /tmp/cua-term-XXXX)
sock=$dir/d.sock
pidf=$dir/d.pid
rec=$dir/rec
"$BIN" serve --socket "$sock" --pid-file "$pidf" --no-overlay --no-permissions-gate >"$dir/serve.log" 2>&1 &
pid=$!
if ! wait_ready "$BIN" "$sock" "$pid"; then
  echo "daemon never became ready"; cat "$dir/serve.log"; exit 2
fi
# An anonymous recording, like `cua-driver recording start`, but with video.
python3 - "$sock" "$rec" <<'EOF'
import json, socket, sys
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
request = {"method": "call", "name": "start_recording", "args": {"output_dir": sys.argv[2], "record_video": True}}
s.sendall((json.dumps(request) + "\n").encode())
print("start_recording:", s.makefile().readline()[:300])
EOF
sleep 3
echo "before SIGTERM: ffmpeg recorders: $(pgrep -fc "ffmpeg.*$rec" || true); socket file: $([ -S "$sock" ] && echo present); pid file: $(cat "$pidf" 2>/dev/null)"

kill -TERM "$pid"
wait "$pid"
code=$?
sleep 2
echo "daemon exit status: $code"
left=$(pgrep -fc "ffmpeg.*$rec" || true)
echo "ffmpeg recorders still running: $left"
[ -S "$sock" ] && sock_state=LEFT || sock_state=removed
[ -e "$pidf" ] && pid_state=LEFT || pid_state=removed
echo "socket file: $sock_state"
echo "pid file: $pid_state"
echo "session.json video: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["video"])' "$rec/session.json" 2>&1)"
if ffprobe -v error -show_entries format=duration -of csv=p=0 "$rec/recording.mp4" >"$dir/probe" 2>&1; then
  echo "recording.mp4: playable, duration $(cat "$dir/probe")s"
  playable=1
else
  echo "recording.mp4: NOT playable: $(head -1 "$dir/probe")"
  playable=0
fi
echo "status after SIGTERM: $("$BIN" status --socket "$sock" 2>&1 | head -2 | tr '\n' ' ')"
echo "--- serve.log (last 5 lines)"
tail -5 "$dir/serve.log"
pkill -f "ffmpeg.*$rec" 2>/dev/null
[ "$code" = 0 ] && [ "$left" = 0 ] && [ "$sock_state" = removed ] && [ "$pid_state" = removed ] && [ "$playable" = 1 ]
