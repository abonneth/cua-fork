#!/usr/bin/env bash
# Start `cua-driver serve` through the pip-installed Python wrapper, SIGTERM
# the pid the shell holds, and check whether the real daemon is still running.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
. "$here/common.sh"
BIN=$(realpath "$1")
SDK=$(realpath "$2")
PKG=$(realpath "$3")

mkdir -p "$PKG/src/cua_driver/bin"
cp "$BIN" "$PKG/src/cua_driver/bin/cua-driver"
cp "$SDK" "$PKG/src/cua_driver/"
python3 -m venv /tmp/cua-venv
/tmp/cua-venv/bin/pip install -q "$PKG"
/tmp/cua-venv/bin/python -m pip show cua-driver | head -2

dir=$(mktemp -d /tmp/cua-py-XXXX)
sock=$dir/d.sock
/tmp/cua-venv/bin/cua-driver serve --socket "$sock" --pid-file "$dir/d.pid" --no-overlay --no-permissions-gate >"$dir/serve.log" 2>&1 &
held=$!
if ! wait_ready "$BIN" "$sock" "$held"; then
  echo "daemon never became ready"; cat "$dir/serve.log"; exit 2
fi
echo "pid the shell holds: $held ($(ps -o comm= -p "$held"))"
echo "daemon pid from pid file: $(cat "$dir/d.pid")"
kill -TERM "$held"
wait "$held"
echo "held process exit status: $?"
sleep 2
left=$(pgrep -f "cua_driver/bin/cua-driver serve --socket $sock" | tr '\n' ' ')
echo "daemon processes still running after SIGTERM: ${left:-none}"
echo "status after SIGTERM: $("$BIN" status --socket "$sock" 2>&1 | head -1)"
[ -n "$left" ] && kill $left
[ -z "$left" ]
