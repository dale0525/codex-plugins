#!/usr/bin/env sh
# Resolve the immutable bundled CLI from the versioned local cache.
# Normal execution never reads or writes global Codex MCP configuration.
set -eu

version="${CREATIVE_MODEL_BRIDGE_VERSION:-0.2.0}"
case "$version" in
  *[!0-9.]*|"") echo "creative-model-bridge: invalid version" >&2; exit 1 ;;
esac
printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "creative-model-bridge: invalid version" >&2; exit 1; }

mode="run"
case "${1:-}" in
  run|cli|exec) mode="run"; shift ;;
  cache) mode="cache"; shift ;;
  install) mode="install"; shift ;;
  migrate) mode="migrate"; shift ;;
  "") ;;
  *) echo "creative-model-bridge: expected run, cache, install, or migrate" >&2; exit 2 ;;
esac

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
override_binary="${CREATIVE_MODEL_BRIDGE_BIN:-}"

if [ "$mode" = "run" ] && [ -n "$override_binary" ]; then
  [ -f "$override_binary" ] && [ -x "$override_binary" ] || { echo "creative-model-bridge: override is not executable" >&2; exit 1; }
  exec "$override_binary" run "$@"
fi
if [ "$mode" = "migrate" ] && [ -n "$override_binary" ]; then
  [ -f "$override_binary" ] && [ -x "$override_binary" ] || { echo "creative-model-bridge: override is not executable" >&2; exit 1; }
  exec "$override_binary" migrate "$@"
fi

system="$(uname -s 2>/dev/null || printf unknown)"
machine="$(uname -m 2>/dev/null || printf unknown)"
windows_shell=0
case "$system" in
  MSYS*|MINGW*|CYGWIN*|Windows_NT*) windows_shell=1 ;;
esac
if [ "$windows_shell" -eq 1 ]; then
  powershell_command=""
  for candidate in powershell.exe powershell pwsh; do
    if command -v "$candidate" >/dev/null 2>&1; then powershell_command="$candidate"; break; fi
  done
  [ -n "$powershell_command" ] || { echo "creative-model-bridge: PowerShell is required on Windows" >&2; exit 1; }
  provisioner="$script_dir/provision.ps1"
  if command -v cygpath >/dev/null 2>&1; then provisioner="$(cygpath -w "$provisioner")"; fi
  exec "$powershell_command" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$provisioner" "$mode" "$@"
fi

case "$system-$machine" in
  Darwin-arm64) target="aarch64-apple-darwin"; asset="creative-model-bridge-aarch64-apple-darwin" ;;
  Darwin-x86_64) target="x86_64-apple-darwin"; asset="creative-model-bridge-x86_64-apple-darwin" ;;
  Linux-x86_64) target="x86_64-unknown-linux-gnu"; asset="creative-model-bridge-x86_64-unknown-linux-gnu" ;;
  Linux-aarch64|Linux-arm64) target="aarch64-unknown-linux-gnu"; asset="creative-model-bridge-aarch64-unknown-linux-gnu" ;;
  *) echo "creative-model-bridge: unsupported platform $system-$machine (use provision.ps1 on Windows)" >&2; exit 1 ;;
esac

if [ -n "${CODEX_HOME:-}" ]; then
  codex_home="$CODEX_HOME"
elif [ -n "${HOME:-}" ]; then
  codex_home="$HOME/.codex"
else
  echo "creative-model-bridge: CODEX_HOME or HOME is required" >&2
  exit 1
fi
mkdir -p "$codex_home"
codex_home="$(CDPATH='' cd -P -- "$codex_home" && pwd)"

if command -v sha256sum >/dev/null 2>&1; then
  hash_file() { sha256sum "$1" | awk '{print $1}'; }
else
  hash_file() { shasum -a 256 "$1" | awk '{print $1}'; }
fi

root="$codex_home/creative-model-bridge/runtime/v$version"
target_root="$root/objects/$target"
active="$target_root/active"
mkdir -p "$target_root" "$target_root/retired"
binary=""
active_digest=""
valid_object() {
  object="$target_root/$1/$2"
  [ -f "$object/complete" ] || return 1
  [ "$(sed -n '1p' "$object/complete" 2>/dev/null)" = "cmb-object-v4" ] || return 1
  [ "$(sed -n '2p' "$object/complete" 2>/dev/null)" = "$1" ] || return 1
  [ "$(sed -n '3p' "$object/complete" 2>/dev/null)" = "$2" ] || return 1
}
valid_digest() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
valid_generation() { [ -n "$1" ] && [ "$1" != "." ] && [ "$1" != ".." ] && printf '%s\n' "$1" | grep -Eq '^[A-Za-z0-9._-]+$'; }
read_active() {
  [ -f "$active" ] || return 1
  [ "$(sed -n '1p' "$active" 2>/dev/null)" = "cmb-active-v4" ] || return 1
  digest="$(sed -n '2p' "$active")"; generation="$(sed -n '3p' "$active")"
  valid_digest "$digest" && valid_generation "$generation" || return 1
  valid_object "$digest" "$generation" || return 1
  candidate="$target_root/$digest/$generation/$asset"
  [ -f "$candidate" ] || return 1
  [ "$(hash_file "$candidate")" = "$digest" ] || return 1
  binary="$candidate"
  active_digest="$digest"
}

override_digest=""
if [ "$mode" = "cache" ] || [ "$mode" = "install" ]; then
  if [ -n "$override_binary" ]; then
    [ -f "$override_binary" ] && [ -x "$override_binary" ] || { echo "creative-model-bridge: override is not executable" >&2; exit 1; }
    override_digest="$(hash_file "$override_binary")"
    valid_digest "$override_digest" || { echo "creative-model-bridge: override digest is invalid" >&2; exit 1; }
  fi
fi
read_active || true

local_stage=""
cleanup_local() {
  if [ -n "$local_stage" ]; then rm -rf "$local_stage"; fi
}
seed_override() {
  local_token="local.$$.$(date +%s 2>/dev/null || printf 0)"
  local_stage="$target_root/staging.$local_token"
  generation="$local_token"
  object="$target_root/$override_digest/$generation"
  attempt=0
  while [ -e "$object" ]; do
    attempt=$((attempt + 1)); generation="$local_token.$attempt"; object="$target_root/$override_digest/$generation"
  done
  mkdir "$local_stage"
  cp "$override_binary" "$local_stage/$asset"
  chmod 0755 "$local_stage/$asset"
  mkdir -p "$object"
  mv "$local_stage/$asset" "$object/$asset"
  printf 'cmb-object-v4\n%s\n%s\n' "$override_digest" "$generation" > "$object/.complete.$local_token"
  mv "$object/.complete.$local_token" "$object/complete"
  printf 'cmb-active-v4\n%s\n%s\n' "$override_digest" "$generation" > "$target_root/.active.$local_token"
  mv "$target_root/.active.$local_token" "$active"
  local_stage=""
  binary="$object/$asset"
  active_digest="$override_digest"
}

if [ -n "$override_binary" ] && { [ "$mode" = "cache" ] || [ "$mode" = "install" ]; } && [ "$active_digest" != "$override_digest" ]; then
  trap cleanup_local EXIT HUP INT TERM
  seed_override
  trap - EXIT HUP INT TERM
  cleanup_local
fi

if [ -z "$binary" ]; then
  [ "${CREATIVE_MODEL_BRIDGE_OFFLINE:-0}" = "1" ] && { echo "creative-model-bridge: cached runtime is unavailable (offline mode)" >&2; exit 1; }
  command -v curl >/dev/null 2>&1 || { echo "creative-model-bridge: curl is required" >&2; exit 1; }
  release="https://github.com/dale0525/codex-plugins/releases/download/creative-model-bridge-v$version"
  lock="$target_root/.download.lock"
  attempts=0; lock_owned=0
  lock_token="$$.$(date +%s 2>/dev/null || printf 0)"
  release_lock() {
    [ "$lock_owned" -eq 1 ] || return 0
    marker="$lock/owner.$lock_token"
    [ -f "$marker" ] || return 0
    retired="$target_root/retired-lock.$lock_token"
    mv "$lock" "$retired" 2>/dev/null || return 0
    lock_owned=0
    rmdir "$retired" 2>/dev/null || rm -rf "$retired"
  }
  cleanup() { release_lock; rm -rf "$target_root/staging.$lock_token"; }
  trap cleanup EXIT HUP INT TERM
  while ! mkdir "$lock" 2>/dev/null; do
    attempts=$((attempts + 1)); [ "$attempts" -gt 600 ] && { echo "creative-model-bridge: timed out waiting for download" >&2; exit 1; }
    owner="$(for marker in "$lock"/owner.*; do [ -f "$marker" ] && cat "$marker"; done 2>/dev/null || true)"
    owner_pid="$(printf '%s\n' "$owner" | awk -F= '$1=="pid"{print $2}')"
    stale_lock=0
    if [ -n "$owner_pid" ] && ! kill -0 "$owner_pid" 2>/dev/null; then stale_lock=1
    elif [ -z "$owner_pid" ] && find "$lock" -prune -mmin +5 -print -quit 2>/dev/null | grep -q .; then stale_lock=1; fi
    if [ "$stale_lock" -eq 1 ]; then mv "$lock" "$target_root/retired-lock-stale.$lock_token.$attempts" 2>/dev/null || true; fi
    sleep 0.1
  done
  lock_owned=1
  printf 'pid=%s\ntoken=%s\nstarted=%s\n' "$$" "$lock_token" "$(date +%s 2>/dev/null || printf 0)" > "$lock/owner.$lock_token"
  read_active || true
  if [ -z "$binary" ]; then
    stage="$target_root/staging.$lock_token"; mkdir "$stage"
    curl --fail --location --silent --show-error "$release/$asset" --output "$stage/$asset"
    curl --fail --location --silent --show-error "$release/checksums.txt" --output "$stage/checksums.txt"
    expected="$(awk -v name="$asset" '$2==name && $1 ~ /^[0-9a-f]{64}$/ {print $1}' "$stage/checksums.txt")"
    [ "$(printf '%s\n' "$expected" | awk 'NF{n++}END{print n+0}')" -eq 1 ] || { echo "creative-model-bridge: invalid checksum entry" >&2; exit 1; }
    digest="$(hash_file "$stage/$asset")"; [ "$digest" = "$expected" ] || { echo "creative-model-bridge: checksum verification failed" >&2; exit 1; }
    generation="$lock_token"; object="$target_root/$digest/$generation"; mkdir -p "$object"
    chmod 0755 "$stage/$asset"; mv "$stage/$asset" "$object/$asset"
    printf 'cmb-object-v4\n%s\n%s\n' "$digest" "$generation" > "$object/complete"
    printf 'cmb-active-v4\n%s\n%s\n' "$digest" "$generation" > "$target_root/.active.$lock_token"
    mv "$target_root/.active.$lock_token" "$active"
    binary="$object/$asset"
  fi
  release_lock; trap - EXIT HUP INT TERM; rm -rf "$target_root/staging.$lock_token"
fi

if [ "$mode" = "cache" ]; then exit 0; fi
if [ "$mode" = "install" ]; then exec "$binary" migrate --codex-home "$codex_home" "$@"; fi
if [ "$mode" = "migrate" ]; then exec "$binary" migrate "$@"; fi
exec "$binary" run "$@"
