#!/usr/bin/env bash

set -u

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$BASE_DIR/.venv/bin/python"
MAIN_SCRIPT="$BASE_DIR/main_bot.py"
DATA_DIR="$BASE_DIR/data"
LOG_DIR="$BASE_DIR/logs"
PID_FILE="$DATA_DIR/main.pid"
MAIN_LOG="$LOG_DIR/main.log"
LOCK_FILE="$DATA_DIR/keep_alive.lock"

mkdir -p "$DATA_DIR" "$LOG_DIR"

with_lock() {
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$LOCK_FILE"
        flock -w 30 9 || {
            echo "Could not acquire keep-alive lock." >&2
            exit 1
        }
    fi
}

project_pids() {
    local script="$1" pid command cwd candidate_pids
    candidate_pids="$(pgrep -u "$(id -u)" -f "[p]ython.*${script//./\\.}" || true)"
    for pid in $candidate_pids; do
        [ -n "$pid" ] || continue
        command="$(ps -p "$pid" -o args= 2>/dev/null || true)"
        cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
        if [[ "$command" == *"$BASE_DIR/$script"* ]] ||
           { [[ "$cwd" == "$BASE_DIR" ]] && [[ "$command" == *"$script"* ]]; }; then
            printf '%s\n' "$pid"
        fi
    done
}

main_pids() { project_pids "main_bot.py"; }
helper_pids() { project_pids "helper_bot.py"; }
self_pids() { project_pids "self_bot.py"; }
first_main_pid() { main_pids | head -n 1; }

start_main() {
    local pid setsid_bin
    pid="$(first_main_pid)"
    if [ -n "$pid" ]; then
        printf '%s\n' "$pid" > "$PID_FILE"
        echo "main_bot.py is already running (PID $pid)."
        return 0
    fi
    if [ ! -x "$PYTHON_BIN" ]; then
        echo "Python virtual environment was not found: $PYTHON_BIN" >&2
        return 1
    fi
    if [ ! -f "$MAIN_SCRIPT" ]; then
        echo "main_bot.py was not found: $MAIN_SCRIPT" >&2
        return 1
    fi
    setsid_bin="$(command -v setsid || true)"
    if [ -n "$setsid_bin" ]; then
        "$setsid_bin" -f "$PYTHON_BIN" -u "$MAIN_SCRIPT" >> "$MAIN_LOG" 2>&1 </dev/null
    else
        nohup "$PYTHON_BIN" -u "$MAIN_SCRIPT" >> "$MAIN_LOG" 2>&1 </dev/null &
    fi
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        pid="$(first_main_pid)"
        if [ -n "$pid" ]; then
            printf '%s\n' "$pid" > "$PID_FILE"
            echo "main_bot.py started (PID $pid)."
            return 0
        fi
    done
    echo "main_bot.py exited during startup. Recent log:" >&2
    tail -n 40 "$MAIN_LOG" >&2 || true
    return 1
}

terminate_list() {
    local pid
    local -a pids=("$@")
    [ "${#pids[@]}" -gt 0 ] || return 0
    kill "${pids[@]}" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        local alive=0
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then alive=1; break; fi
        done
        [ "$alive" -eq 0 ] && return 0
    done
    for pid in "${pids[@]}"; do
        kill -KILL "$pid" 2>/dev/null || true
    done
}

stop_all() {
    local -a mains=() helpers=() selfs=()
    mapfile -t mains < <(main_pids)
    terminate_list "${mains[@]}"
    mapfile -t helpers < <(helper_pids)
    mapfile -t selfs < <(self_pids)
    terminate_list "${helpers[@]}"
    terminate_list "${selfs[@]}"
    rm -f "$PID_FILE"
    echo "main, helper and self-bot processes stopped."
}

show_status() {
    local main_pid helper_count self_count
    main_pid="$(first_main_pid)"
    helper_count="$(helper_pids | wc -l | tr -d ' ')"
    self_count="$(self_pids | wc -l | tr -d ' ')"
    if [ -n "$main_pid" ]; then
        echo "main API bot: running (PID $main_pid)"
    else
        echo "main API bot: stopped"
    fi
    echo "helper API bot processes: $helper_count"
    echo "self-bot processes: $self_count"
}

case "${1:-status}" in
    start)
        with_lock
        start_main
        ;;
    stop)
        with_lock
        stop_all
        ;;
    restart)
        with_lock
        stop_all
        start_main
        ;;
    watch)
        with_lock
        start_main >/dev/null
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|watch|status}" >&2
        exit 2
        ;;
esac
