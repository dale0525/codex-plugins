#!/usr/bin/env sh
# Download and launch only the pinned Gortex release.
set -eu

plugin_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
metadata="$plugin_root/runtime-release.json"

log() {
  printf '%s\n' "$*" >&2
}

json_root_string() {
  awk -v field="$1" '
    $0 ~ "^  \\\"" field "\\\": \\\"" {
      value = $0
      sub("^  \\\"" field "\\\": \\\"", "", value)
      sub("\\\",?$", "", value)
      print value
      exit
    }
  ' "$metadata"
}

json_asset_value() {
  awk -v target="$1" -v field="$2" '
    $0 ~ "^    \\\"" target "\\\": \\{$" { in_target = 1; next }
    in_target && $0 ~ "^    \\}" { exit }
    in_target && $0 ~ "^      \\\"" field "\\\":" {
      value = $0
      sub("^      \\\"" field "\\\": ", "", value)
      sub(/,$/, "", value)
      sub(/^\"/, "", value)
      sub(/\"$/, "", value)
      print value
      exit
    }
  ' "$metadata"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    log "gortex requires sha256sum or shasum for archive verification"
    return 1
  fi
}

valid_version() {
  candidate=$1
  [ -x "$candidate" ] || return 1
  actual=$("$candidate" version --short 2>/dev/null || true)
  case "$actual" in
    "$version"|"v$version"|"$version"+*|"v$version"+*) return 0 ;;
    *) return 1 ;;
  esac
}

exec_runtime() {
  runtime_binary=$1
  shift
  exec "$runtime_binary" mcp "$@"
}

safe_archive_paths() {
  case "$archive_kind" in
    tar.gz) tar -tzf "$archive" ;;
    zip)
      if ! command -v unzip >/dev/null 2>&1; then
        log "gortex requires unzip to inspect the Windows archive"
        return 1
      fi
      unzip -Z1 "$archive"
      ;;
  esac | awk '
    /^\// || /^[A-Za-z]:\// || /\\/ || /(^|\/)\.\.($|\/)/ { unsafe = 1 }
    END { exit unsafe ? 1 : 0 }
  '
}

extract_archive() {
  case "$archive_kind" in
    tar.gz) tar -xzf "$archive" -C "$extract_dir" ;;
    zip) unzip -q "$archive" -d "$extract_dir" ;;
  esac
}

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    asset_key="gortex_darwin_arm64"
    cache_root="${HOME}/.local/share/gortex"
    binary_name="gortex"
    archive_kind="tar.gz"
    ;;
  MINGW*-x86_64|MSYS*-x86_64)
    asset_key="gortex_windows_amd64"
    if [ -z "${LOCALAPPDATA:-}" ] || ! command -v cygpath >/dev/null 2>&1; then
      log "gortex requires LOCALAPPDATA and cygpath on Windows"
      exit 1
    fi
    local_app_data=$(cygpath -u "$LOCALAPPDATA")
    cache_root="$local_app_data/gortex"
    binary_name="gortex.exe"
    archive_kind="zip"
    ;;
  *)
    log "Unsupported gortex platform: $(uname -s)-$(uname -m) (supported: Darwin arm64, MINGW/MSYS x86_64)"
    exit 1
    ;;
esac

[ -f "$metadata" ] || { log "Missing runtime release metadata: $metadata"; exit 1; }
version=$(json_root_string version)
asset_name=$(json_asset_value "$asset_key" name)
asset_url=$(json_asset_value "$asset_key" url)
expected_size=$(json_asset_value "$asset_key" size)
expected_sha256=$(json_asset_value "$asset_key" sha256)
if [ -z "$version" ] || [ -z "$asset_name" ] || [ -z "$asset_url" ] || [ -z "$expected_size" ] || [ -z "$expected_sha256" ]; then
  log "Incomplete runtime release metadata for $asset_key"
  exit 1
fi

version_dir="$cache_root/versions/$version"
binary="$version_dir/$binary_name"
if valid_version "$binary"; then
  exec_runtime "$binary" "$@"
fi
if [ -e "$version_dir" ]; then
  log "Cached gortex $version failed version verification; refusing to use or replace it"
  exit 1
fi

mkdir -p "$cache_root/versions"
lock_dir="$cache_root/versions/.${version}.lock"
waited=0
while ! mkdir "$lock_dir" 2>/dev/null; do
  if [ ! -d "$lock_dir" ]; then
    log "Cannot acquire gortex installation lock: $lock_dir"
    exit 1
  fi
  if [ "$waited" -ge 120 ]; then
    log "Timed out waiting for gortex $version installation lock"
    exit 1
  fi
  sleep 1
  waited=$((waited + 1))
done

temporary_dir=""
cleanup() {
  status=$?
  if [ -n "$temporary_dir" ] && [ -d "$temporary_dir" ]; then
    rm -rf "$temporary_dir"
  fi
  rmdir "$lock_dir" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

# Another launcher may have published while this process waited for its lock.
if valid_version "$binary"; then
  temporary_dir=""
  trap - EXIT HUP INT TERM
  rmdir "$lock_dir"
  exec_runtime "$binary" "$@"
fi
if [ -e "$version_dir" ]; then
  log "Cached gortex $version failed version verification; refusing to use or replace it"
  exit 1
fi

temporary_dir=$(mktemp -d "$cache_root/versions/.${version}.download.XXXXXX")
archive="$temporary_dir/$asset_name"
extract_dir="$temporary_dir/extract"
staged_dir="$temporary_dir/$version"

log "Downloading verified gortex $version for $asset_key"
curl --fail --location --silent --show-error --header 'Cache-Control: no-cache' "$asset_url" --output "$archive"
actual_size=$(wc -c < "$archive" | tr -d '[:space:]')
if [ "$actual_size" != "$expected_size" ]; then
  log "Archive size verification failed for $asset_name"
  exit 1
fi
actual_sha256=$(sha256_file "$archive")
if [ "$actual_sha256" != "$expected_sha256" ]; then
  log "Archive SHA-256 verification failed for $asset_name"
  exit 1
fi
if ! safe_archive_paths; then
  log "Archive contains an unsafe extraction path: $asset_name"
  exit 1
fi

mkdir "$extract_dir" "$staged_dir"
extract_archive
downloaded_binary=$(find "$extract_dir" -type f -name "$binary_name" -print | sed -n '1p')
if [ -z "$downloaded_binary" ]; then
  log "Archive does not contain $binary_name"
  exit 1
fi
cp "$downloaded_binary" "$staged_dir/$binary_name"
chmod 0755 "$staged_dir/$binary_name"
if ! valid_version "$staged_dir/$binary_name"; then
  log "Downloaded gortex $version failed version verification"
  exit 1
fi

mv "$staged_dir" "$version_dir"
rm -rf "$temporary_dir"
temporary_dir=""
trap - EXIT HUP INT TERM
rmdir "$lock_dir"
exec_runtime "$binary" "$@"
