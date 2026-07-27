#!/usr/bin/env sh
set -eu

version="${CODEX_SYNC_VERSION:-0.4.0}"
codex_home_path="${CODEX_HOME:-${HOME}/.codex}"
install_directory="${CODEX_SYNC_BIN_HOME:-${codex_home_path}/codex-sync/bin/${version}}"

if [ -n "${CODEX_SYNC_BIN:-}" ]; then
  exec "${CODEX_SYNC_BIN}" "$@"
fi

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) target="aarch64-apple-darwin" ;;
  Darwin-x86_64) target="x86_64-apple-darwin" ;;
  Linux-x86_64) target="x86_64-unknown-linux-gnu" ;;
  Linux-aarch64|Linux-arm64) target="aarch64-unknown-linux-gnu" ;;
  *) echo "Unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

binary_path="${install_directory}/codex-sync"
if [ ! -x "${binary_path}" ]; then
  if [ "${CODEX_SYNC_OFFLINE:-0}" = "1" ]; then
    echo "Codex Sync engine is not cached and offline mode forbids downloading it" >&2
    exit 1
  fi
  temporary_directory="$(mktemp -d)"
  trap 'rm -rf "${temporary_directory}"' EXIT HUP INT TERM
  artifact="codex-sync-${target}"
  release_base="https://github.com/dale0525/codex-plugins/releases/download/codex-sync-v${version}"
  curl --fail --location --silent --show-error \
    "${release_base}/${artifact}" --output "${temporary_directory}/${artifact}"
  curl --fail --location --silent --show-error \
    "${release_base}/checksums.txt" --output "${temporary_directory}/checksums.txt"
  expected="$(awk -v name="${artifact}" '$2 == name { print $1 }' "${temporary_directory}/checksums.txt")"
  if [ -z "${expected}" ]; then
    echo "Release checksum is missing for ${artifact}" >&2
    exit 1
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "${temporary_directory}/${artifact}" | awk '{print $1}')"
  else
    actual="$(shasum -a 256 "${temporary_directory}/${artifact}" | awk '{print $1}')"
  fi
  if [ "${actual}" != "${expected}" ]; then
    echo "Checksum verification failed for ${artifact}" >&2
    exit 1
  fi
  mkdir -p "${install_directory}"
  mv "${temporary_directory}/${artifact}" "${binary_path}"
  chmod 0755 "${binary_path}"
fi

exec "${binary_path}" "$@"
