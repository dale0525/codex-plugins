#!/bin/sh
set -eu
export PYTHONUTF8=1

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

find_python() (
  set -f
  IFS=:
  for directory in ${PATH-}; do
    [ -n "$directory" ] || directory=.
    candidate="$directory/python3"
    if [ -x "$candidate" ] &&
      "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      exit 0
    fi
  done
  for directory in ${PATH-}; do
    [ -n "$directory" ] || directory=.
    candidate="$directory/python"
    if [ -x "$candidate" ] &&
      "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      exit 0
    fi
  done
  exit 1
)

python_executable=$(find_python || true)
if [ -n "$python_executable" ]; then
  exec "$python_executable" "$script_dir/provider_chat_completions.py" "$@"
fi
printf '%s\n' '{"ok":false,"stage":"runtime","code":"python_unavailable","retryable":false}'
exit 1
