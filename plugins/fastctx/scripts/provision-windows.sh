#!/usr/bin/env sh
set -eu

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *)
    echo "This bridge must run from Git Bash on Windows" >&2
    exit 2
    ;;
esac

script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if command -v pwsh.exe >/dev/null 2>&1; then
  powershell=$(command -v pwsh.exe)
elif command -v pwsh >/dev/null 2>&1; then
  powershell=$(command -v pwsh)
else
  echo "PowerShell 7 (pwsh) is required to run the FastCtx Windows provisioner" >&2
  exit 1
fi

provisioner="$script_directory/provision.ps1"
if command -v cygpath >/dev/null 2>&1; then
  provisioner=$(cygpath -w "$provisioner")
fi

exec "$powershell" \
  -NoProfile \
  -NonInteractive \
  -ExecutionPolicy Bypass \
  -File "$provisioner" \
  "$@"
