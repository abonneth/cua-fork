#!/usr/bin/env bash
# Exhaust the daemon's descriptors with idle connections, then check it is
# still alive and answers once they close.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/common.sh"
BIN=$(realpath "$1")
LIMIT=${2:-64}

dir=$(mktemp -d /tmp/cua-accept-XXXX)
sock=$dir/d.sock
(ulimit -n "$LIMIT"; exec "$BIN" serve --socket "$sock" --pid-file "$dir/d.pid" --no-overlay --no-permissions-gate) >"$dir/serve.log" 2>&1 &
pid=$!
if ! wait_ready "$BIN" "$sock" "$pid"; then
  echo "daemon never became ready"; cat "$dir/serve.log"; exit 2
fi
echo "daemon pid $pid ready; ulimit -n $LIMIT; open fds at idle: $(fds "$pid")"

python3 "$here/hold.py" "$sock" $((LIMIT + 64)) 4 &
holder=$!
sleep 2
if kill -0 "$pid" 2>/dev/null; then
  echo "during exhaustion: daemon ALIVE (open fds $(fds "$pid"))"
else
  echo "during exhaustion: daemon EXITED"
fi
wait "$holder"
sleep 1
alive=0
if kill -0 "$pid" 2>/dev/null; then
  alive=1; echo "after connections closed: daemon ALIVE"
else
  wait "$pid"; echo "after connections closed: daemon GONE (exit $?)"
fi
"$BIN" status --socket "$sock" >"$dir/status.out" 2>&1
st=$?
echo "fresh status request: exit $st: $(head -2 "$dir/status.out" | tr '\n' ' ')"
echo "--- serve.log (last 5 lines)"
tail -5 "$dir/serve.log"
kill "$pid" 2>/dev/null
[ "$alive" = 1 ] && [ "$st" = 0 ]
