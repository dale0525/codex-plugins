#!/usr/bin/env sh
# Download the immutable release executable on first use.  This launcher is
# intentionally dependency-light and does not require Git, Pixi, or Python.
set -eu

version="${CREATIVE_MODEL_BRIDGE_VERSION:-0.1.9}"
case "$version" in
  *[!0-9.]*|"") echo "creative-model-bridge: invalid version" >&2; exit 1 ;;
esac
printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "creative-model-bridge: invalid version" >&2; exit 1; }

if [ -n "${CREATIVE_MODEL_BRIDGE_BIN:-}" ]; then
  binary="$CREATIVE_MODEL_BRIDGE_BIN"
  [ -f "$binary" ] && [ -x "$binary" ] || { echo "creative-model-bridge: override is not executable" >&2; exit 1; }
  export CREATIVE_MODEL_BRIDGE_EXECUTABLE="$binary"
  [ "$#" -gt 0 ] || set -- setup --yes
  exec "$binary" provision "$@"
fi

system="$(uname -s 2>/dev/null || printf unknown)"
machine="$(uname -m 2>/dev/null || printf unknown)"
case "$system-$machine" in
  Darwin-arm64) target="aarch64-apple-darwin"; asset="creative-model-bridge-aarch64-apple-darwin" ;;
  Darwin-x86_64) target="x86_64-apple-darwin"; asset="creative-model-bridge-x86_64-apple-darwin" ;;
  Linux-x86_64) target="x86_64-unknown-linux-gnu"; asset="creative-model-bridge-x86_64-unknown-linux-gnu" ;;
  Linux-aarch64|Linux-arm64) target="aarch64-unknown-linux-gnu"; asset="creative-model-bridge-aarch64-unknown-linux-gnu" ;;
  *) echo "creative-model-bridge: unsupported platform $system-$machine (use provision.ps1 on Windows)" >&2; exit 1 ;;
esac

codex_home="${CODEX_HOME:-${HOME:-}/.codex}"
[ -n "$codex_home" ] || { echo "creative-model-bridge: CODEX_HOME or HOME is required" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "creative-model-bridge: curl is required" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then hash_file() { sha256sum "$1" | awk '{print $1}'; }; else hash_file() { shasum -a 256 "$1" | awk '{print $1}'; }; fi

root="$codex_home/creative-model-bridge/runtime/v$version"
target_root="$root/objects/$target"
active="$target_root/active"
mkdir -p "$target_root" "$target_root/retired"
binary=""
valid_object() {
  object="$target_root/$1/$2"
  [ -f "$object/complete" ] || return 1
  [ "$(sed -n '1p' "$object/complete" 2>/dev/null)" = "cmb-object-v4" ] || return 1
  [ "$(sed -n '2p' "$object/complete" 2>/dev/null)" = "$1" ] || return 1
  [ "$(sed -n '3p' "$object/complete" 2>/dev/null)" = "$2" ] || return 1
}
valid_digest() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
valid_generation() { [ -n "$1" ] && [ "$1" != "." ] && [ "$1" != ".." ] && printf '%s\n' "$1" | grep -Eq '^[A-Za-z0-9._-]+$'; }
if [ -f "$active" ] && [ "$(sed -n '1p' "$active" 2>/dev/null)" = "cmb-active-v4" ]; then
  digest="$(sed -n '2p' "$active")"; generation="$(sed -n '3p' "$active")"
  valid_digest "$digest" || digest=""
  valid_generation "$generation" || generation=""
  if [ -n "$digest" ] && [ -n "$generation" ] && valid_object "$digest" "$generation" && [ -f "$target_root/$digest/$generation/$asset" ] && [ "$(hash_file "$target_root/$digest/$generation/$asset")" = "$digest" ]; then
    binary="$target_root/$digest/$generation/$asset"
  fi
fi

if [ -z "$binary" ]; then
  [ "${CREATIVE_MODEL_BRIDGE_OFFLINE:-0}" = "1" ] && { echo "creative-model-bridge: cached runtime is unavailable (offline mode)" >&2; exit 1; }
  release="https://github.com/dale0525/codex-plugins/releases/download/creative-model-bridge-v$version"
  lock="$target_root/.download.lock"
  attempts=0
  lock_owned=0
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
    if [ -n "$owner_pid" ] && ! kill -0 "$owner_pid" 2>/dev/null; then
      stale_lock=1
    elif [ -z "$owner_pid" ] && find "$lock" -prune -mmin +5 -print -quit 2>/dev/null | grep -q .; then
      stale_lock=1
    fi
    if [ "$stale_lock" -eq 1 ]; then
      mv "$lock" "$target_root/retired-lock-stale.$lock_token.$attempts" 2>/dev/null || true
    fi
    sleep 0.1
  done
  lock_owned=1
  printf 'pid=%s\ntoken=%s\nstarted=%s\n' "$$" "$lock_token" "$(date +%s 2>/dev/null || printf 0)" > "$lock/owner.$lock_token"
  # Another process may have completed while we waited.
  if [ -f "$active" ] && [ "$(sed -n '1p' "$active" 2>/dev/null)" = "cmb-active-v4" ]; then
    digest="$(sed -n '2p' "$active")"; generation="$(sed -n '3p' "$active")"
    valid_digest "$digest" || digest=""
    valid_generation "$generation" || generation=""
    if valid_object "$digest" "$generation" && [ -f "$target_root/$digest/$generation/$asset" ] && [ "$(hash_file "$target_root/$digest/$generation/$asset")" = "$digest" ]; then binary="$target_root/$digest/$generation/$asset"; fi
  fi
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
  release_lock
  trap - EXIT HUP INT TERM
  rm -rf "$target_root/staging.$lock_token"
fi

export CREATIVE_MODEL_BRIDGE_EXECUTABLE="$binary"
[ "$#" -gt 0 ] || set -- setup --yes
exec "$binary" provision "$@"
