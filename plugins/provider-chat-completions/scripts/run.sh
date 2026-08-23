#!/bin/sh
set -eu
export PYTHONUTF8=1

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1 &&
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
  exec python3 "$script_dir/provider_chat_completions.py" "$@"
fi
if command -v python >/dev/null 2>&1 &&
  python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
  exec python "$script_dir/provider_chat_completions.py" "$@"
fi
printf '%s\n' '{"ok":false,"stage":"runtime","code":"python_unavailable","retryable":false}'
exit 1
