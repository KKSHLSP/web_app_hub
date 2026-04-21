#!/bin/sh
set -eu

BASE_DIR="/Users/allenhsu/Desktop/forfun/web_app_hub"
SCRIPT_PATH="$BASE_DIR/launch_reader_ai_batch.sh"
READER_BATCH="$BASE_DIR/reader_ai_batch.py"
PYTHON_BIN="/opt/homebrew/bin/python3"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

run_inside_screen() {
  log_path="$1"
  shift
  mkdir -p "$(dirname "$log_path")"
  exec >>"$log_path" 2>&1
  echo "[launcher] started_at=$(date '+%Y-%m-%dT%H:%M:%S%z')"
  echo "[launcher] cwd=$BASE_DIR"
  echo "[launcher] python=$PYTHON_BIN"
  echo "[launcher] args=$*"
  cd "$BASE_DIR"
  if command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -dimsu "$PYTHON_BIN" "$READER_BATCH" "$@"
  fi
  exec "$PYTHON_BIN" "$READER_BATCH" "$@"
}

if [ "${1:-}" = "--inside-screen" ]; then
  shift
  run_inside_screen "$@"
fi

session_name="reader_ai_night"
run_dir="$BASE_DIR/data/reader_ai_runs/night_$(date '+%Y%m%d_%H%M%S')"

while [ $# -gt 0 ]; do
  case "$1" in
    --session)
      session_name="$2"
      shift 2
      ;;
    --run-dir)
      run_dir="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if screen -ls 2>/dev/null | grep -q "[.]${session_name}[[:space:]]"; then
  echo "screen session already exists: $session_name" >&2
  exit 1
fi

mkdir -p "$run_dir"
log_path="$run_dir/stdout.log"

if [ $# -eq 0 ]; then
  set -- --mode auto --timeout 120 --retry-count 2 --retry-backoff-sec 2.5
fi

screen -dmS "$session_name" /bin/sh "$SCRIPT_PATH" --inside-screen "$log_path" --run-dir "$run_dir" "$@"
echo "session_name=$session_name"
echo "run_dir=$run_dir"
echo "log_path=$log_path"
