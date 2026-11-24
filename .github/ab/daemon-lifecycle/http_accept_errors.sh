#!/usr/bin/env bash
# Exhaust the daemon's descriptors with connections to each HTTP listener and
# check the listener still answers afterwards (envelope HTTP) or did not spin
# (MCP HTTP).
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/common.sh"
BIN=$(realpath "$1")
LIMIT=${2:-64}
ENVELOPE=18431
MCP=18432

dir=$(mktemp -d /tmp/cua-http-XXXX)
sock=$dir/d.sock
export CUA_DRIVER_ENVELOPE_HTTP_PORT=$ENVELOPE CUA_DRIVER_RS_MCP_HTTP_PORT=$MCP
export CUA_DRIVER_RS_MCP_HTTP_TOKEN=abcdefghijklmnopqrstuvwxyz0123456789ABCD
(ulimit -n "$LIMIT"; exec "$BIN" serve --socket "$sock" --pid-file "$dir/d.pid" --no-overlay --no-permissions-gate) >"$dir/serve.log" 2>&1 &
pid=$!
if ! wait_ready "$BIN" "$sock" "$pid"; then
  echo "daemon never became ready"; cat "$dir/serve.log"; exit 2
fi
sleep 1
code() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$@" || true; }
cpu() { awk '{print $14 + $15}' "/proc/$1/stat"; }
echo "daemon pid $pid ready; ulimit -n $LIMIT; open fds at idle: $(fds "$pid")"
echo "envelope HTTP before: HTTP $(code http://127.0.0.1:$ENVELOPE/)"
echo "MCP HTTP before: HTTP $(code -X POST http://127.0.0.1:$MCP/mcp)"

python3 "$here/hold.py" "tcp:$ENVELOPE" $((LIMIT + 64)) 4
sleep 1
envelope_after=$(code http://127.0.0.1:$ENVELOPE/)
echo "envelope HTTP after connections closed: HTTP $envelope_after (000 = refused)"

before_cpu=$(cpu "$pid")
before_warn=$(grep -c "MCP HTTP accept error\|accept on 127.0.0.1:$MCP" "$dir/serve.log")
python3 "$here/hold.py" "tcp:$MCP" $((LIMIT + 64)) 4
after_cpu=$(cpu "$pid")
after_warn=$(grep -c "MCP HTTP accept error\|accept on 127.0.0.1:$MCP" "$dir/serve.log")
echo "MCP HTTP during 4 s exhaustion: $((after_cpu - before_cpu)) CPU ticks (100/s), $((after_warn - before_warn)) accept warnings"
sleep 1
echo "MCP HTTP after connections closed: HTTP $(code -X POST http://127.0.0.1:$MCP/mcp)"
kill -0 "$pid" 2>/dev/null && echo "daemon: ALIVE" || echo "daemon: GONE"
echo "--- serve.log accept lines (first 3)"
grep -i "accept" "$dir/serve.log" | head -3
kill "$pid" 2>/dev/null
[ "$envelope_after" != 000 ]
