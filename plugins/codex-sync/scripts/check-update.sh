#!/usr/bin/env sh
set -eu

version="${CODEX_SYNC_VERSION:-0.1.0}"
codex_home_path="${CODEX_HOME:-${HOME}/.codex}"
binary_path="${CODEX_SYNC_BIN:-${CODEX_SYNC_BIN_HOME:-${codex_home_path}/codex-sync/bin/${version}}/codex-sync}"

if [ -x "${binary_path}" ]; then
  "${binary_path}" check-update
fi
