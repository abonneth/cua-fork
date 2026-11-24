export CUA_DRIVER_RS_TELEMETRY_ENABLED=false CUA_TELEMETRY=0 DO_NOT_TRACK=1

wait_ready() { # bin socket pid
  for _ in $(seq 1 200); do
    "$1" status --socket "$2" >/dev/null 2>&1 && return 0
    kill -0 "$3" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}

fds() { ls "/proc/$1/fd" 2>/dev/null | wc -l; }
