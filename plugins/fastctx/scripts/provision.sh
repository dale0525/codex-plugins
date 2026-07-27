#!/usr/bin/env sh
# shellcheck disable=SC2218 # Functions are invoked only after all definitions below.
set -eu

plugin_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
metadata="$plugin_root/upstream-release.json"
action="${1:-status}"
codex_home="${CODEX_HOME:-${HOME}/.codex}"
fastctx_dir="${HOME}/.fastctx"
fastctx_config="$fastctx_dir/config.toml"
stable_binary="$fastctx_dir/bin/fastctx"

json_global_string() {
  sed -n "s/^  \"$1\": \"\([^\"]*\)\",\{0,1\}$/\1/p" "$metadata" | head -n 1
}

json_asset_string() {
  awk -v target="$1" -v field="$2" '
    $0 ~ "^    \\\"" target "\\\": \\{$" { in_target = 1; next }
    in_target && $0 ~ "^    \\}" { exit }
    in_target && $0 ~ "^      \\\"" field "\\\":" {
      value = $0
      sub("^      \\\"" field "\\\": \\\"", "", value)
      sub("\\\",?$", "", value)
      print value
      exit
    }
  ' "$metadata"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

fastshell_enabled() {
  [ -f "$fastctx_config" ] || return 1
  awk '
    /^\[fastshell\][[:space:]]*$/ { section = 1; next }
    /^\[/ { section = 0 }
    section && /^[[:space:]]*enabled[[:space:]]*=[[:space:]]*true([[:space:]]*(#.*)?)?$/ { found = 1 }
    END { exit found ? 0 : 1 }
  ' "$fastctx_config"
}

enable_fastshell() {
  mkdir -p "$fastctx_dir"
  temporary="$fastctx_dir/.config.fastctx-provision.$$"
  if [ ! -f "$fastctx_config" ]; then
    {
      printf '%s\n\n' 'schema_version = 1'
      printf '%s\n' '[fastshell]'
      printf '%s\n' 'enabled = true'
    } >"$temporary"
  else
    awk '
      BEGIN { section = 0; seen_section = 0; wrote = 0 }
      /^\[fastshell\][[:space:]]*$/ {
        if (section && !wrote) print "enabled = true"
        section = 1
        seen_section = 1
        wrote = 0
        print
        next
      }
      /^\[/ {
        if (section && !wrote) print "enabled = true"
        section = 0
      }
      section && /^[[:space:]]*enabled[[:space:]]*=/ {
        print "enabled = true"
        wrote = 1
        next
      }
      { print }
      END {
        if (section && !wrote) print "enabled = true"
        if (!seen_section) {
          print ""
          print "[fastshell]"
          print "enabled = true"
        }
      }
    ' "$fastctx_config" >"$temporary"
  fi
  chmod 0600 "$temporary"
  mv "$temporary" "$fastctx_config"
}

download_binary() {
  version=$(json_global_string version)
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) target="aarch64-apple-darwin" ;;
    Darwin-x86_64) target="x86_64-apple-darwin" ;;
    Linux-x86_64) target="x86_64-unknown-linux-gnu" ;;
    *) echo "Unsupported FastCtx platform: $(uname -s)-$(uname -m)" >&2; return 1 ;;
  esac
  asset_name=$(json_asset_string "$target" name)
  asset_url=$(json_asset_string "$target" url)
  expected=$(json_asset_string "$target" sha256)
  if [ -z "$version" ] || [ -z "$asset_name" ] || [ -z "$asset_url" ] || [ -z "$expected" ]; then
    echo "FastCtx release metadata is incomplete for $target" >&2
    return 1
  fi
  mkdir -p "$fastctx_dir"
  temporary_directory=$(mktemp -d "$fastctx_dir/.provision.XXXXXX")
  archive="$temporary_directory/$asset_name"
  curl --fail --location --silent --show-error "$asset_url" --output "$archive"
  actual=$(sha256_file "$archive")
  if [ "$actual" != "$expected" ]; then
    echo "FastCtx archive checksum verification failed for $asset_name" >&2
    return 1
  fi
  if tar -tzf "$archive" | awk '/^(\/|\.\.\/)|\/\.\.\// { bad = 1 } END { exit bad ? 0 : 1 }'; then
    echo "FastCtx archive contains an unsafe path" >&2
    return 1
  fi
  mkdir -p "$temporary_directory/extract"
  tar -xzf "$archive" -C "$temporary_directory/extract"
  downloaded=$(find "$temporary_directory/extract" -type f -name fastctx -print | head -n 1)
  if [ -z "$downloaded" ]; then
    echo "FastCtx archive does not contain the expected executable" >&2
    return 1
  fi
  retained="$temporary_directory/fastctx"
  cp "$downloaded" "$retained"
  chmod 0755 "$retained"
  printf '%s\n' "$retained"
}

setup_fastctx() {
  version=$(json_global_string version)
  if [ -x "$stable_binary" ] && fastshell_enabled; then
    installed_version=$("$stable_binary" --version 2>/dev/null | awk 'NR == 1 { print $2 }')
    if [ "$installed_version" = "$version" ] && FASTCTX_DISABLE_UPDATE_CHECK=1 "$stable_binary" status --codex-home "$codex_home" >/dev/null 2>&1; then
      echo "FastCtx $version is already provisioned with shell tools enabled"
      return 0
    fi
  fi

  downloaded=$(download_binary)
  backup=""
  if [ -f "$fastctx_config" ]; then
    backup=$(mktemp)
    cp "$fastctx_config" "$backup"
  fi
  enable_fastshell
  if ! FASTCTX_DISABLE_UPDATE_CHECK=1 "$downloaded" apply --codex-home "$codex_home" --tier standard --yes; then
    if [ -n "$backup" ]; then
      cp "$backup" "$fastctx_config"
    else
      rm -f "$fastctx_config"
    fi
    rm -f "$backup"
    rm -rf "$(dirname -- "$downloaded")"
    return 1
  fi
  rm -f "$backup"
  rm -rf "$(dirname -- "$downloaded")"
  FASTCTX_DISABLE_UPDATE_CHECK=1 "$stable_binary" status --codex-home "$codex_home"
}

case "$action" in
  setup)
    setup_fastctx
    ;;
  status)
    if [ ! -x "$stable_binary" ]; then
      echo "FastCtx is not installed at $stable_binary" >&2
      exit 1
    fi
    FASTCTX_DISABLE_UPDATE_CHECK=1 "$stable_binary" status --codex-home "$codex_home"
    ;;
  unapply)
    if [ ! -x "$stable_binary" ]; then
      echo "FastCtx is not installed at $stable_binary" >&2
      exit 1
    fi
    FASTCTX_DISABLE_UPDATE_CHECK=1 "$stable_binary" unapply --codex-home "$codex_home" --yes
    ;;
  *)
    echo "Usage: $0 {setup|status|unapply} [--yes]" >&2
    exit 2
    ;;
esac
