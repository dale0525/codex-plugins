use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use assert_cmd::Command as AssertCommand;
use tempfile::TempDir;

pub fn run_git(cwd: &Path, args: &[&str]) {
    let output = Command::new("git")
        .current_dir(cwd)
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {:?}: {}",
        args,
        String::from_utf8_lossy(&output.stderr)
    );
}

pub fn fake_codex(path: &Path) {
    fs::write(
        path,
        r#"#!/bin/sh
set -eu
if [ "${1:-}" = "--version" ]; then echo 'codex test'; exit 0; fi
if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "list" ] && [ -n "${FAKE_CODEX_PLUGINS_JSON:-}" ]; then
  if [ -f "${CODEX_HOME}/.fake-plugin-ops" ]; then
    python3 - "$FAKE_CODEX_PLUGINS_JSON" "${CODEX_HOME}/.fake-plugin-ops" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
ops = [line.rstrip("\n").split("\t", 1) for line in open(sys.argv[2])]
installed = payload.setdefault("installed", [])
for operation, plugin_id in ops:
    if operation == "remove":
        installed[:] = [item for item in installed if item.get("pluginId") != plugin_id]
    elif operation == "add":
        found = next((item for item in installed if item.get("pluginId") == plugin_id), None)
        if found is None:
            installed.append({"pluginId": plugin_id, "installed": True, "enabled": True})
        else:
            found["installed"] = True
            found["enabled"] = True
print(json.dumps(payload))
PY
    exit 0
  fi
  cat "$FAKE_CODEX_PLUGINS_JSON"
  exit 0
fi
if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "list" ] && [ -n "${FAKE_CODEX_MARKETS_JSON:-}" ]; then
  if [ -f "${CODEX_HOME}/.fake-market-ops" ]; then
    python3 - "$FAKE_CODEX_MARKETS_JSON" "${CODEX_HOME}/.fake-market-ops" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
markets = payload.setdefault("marketplaces", [])
for fields in [line.rstrip("\n").split("\t") for line in open(sys.argv[2])]:
    if fields[0] == "remove":
        markets[:] = [item for item in markets if item.get("name") != fields[1]]
    elif fields[0] == "add":
        name, url, ref, sparse = fields[1:]
        markets[:] = [item for item in markets if item.get("name") != name]
        markets.append({"name": name, "marketplaceSource": {"sourceType": "git", "source": url, "ref": ref, "sparse": ([sparse] if sparse else [])}})
print(json.dumps(payload))
PY
    exit 0
  fi
  cat "$FAKE_CODEX_MARKETS_JSON"
  exit 0
fi
if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "list" ]; then echo '{"installed":[]}'; exit 0; fi
if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "list" ]; then echo '{"marketplaces":[]}'; exit 0; fi
if [ "${1:-}" = "plugin" ]; then
  mutation=0
  if [ "${2:-}" = "add" ] || [ "${2:-}" = "remove" ] || [ "${2:-}" = "upgrade" ]; then
    mutation=1
  elif [ "${2:-}" = "marketplace" ]; then
    case "${3:-}" in
      add|remove|upgrade) mutation=1 ;;
    esac
  fi
  if [ "$mutation" -eq 1 ]; then
    if [ -n "${FAKE_CODEX_LOG:-}" ]; then echo "$*" >> "$FAKE_CODEX_LOG"; fi
    if [ "${2:-}" = "add" ] && [ -n "${FAKE_CODEX_FAIL_ID:-}" ] && [ "${3:-}" = "$FAKE_CODEX_FAIL_ID" ]; then exit 17; fi
    if [ "${2:-}" = "add" ] || [ "${2:-}" = "remove" ]; then
      operation=add
      [ "${2:-}" = "remove" ] && operation=remove
      printf '%s\t%s\n' "$operation" "${3:-}" >> "${CODEX_HOME}/.fake-plugin-ops"
    fi
    if [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "add" ]; then
      url="${4:-}"; ref=main; sparse=""
      shift 4
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --ref) ref="${2:-}"; shift 2 ;;
          --sparse) sparse="${2:-}"; shift 2 ;;
          *) shift ;;
        esac
      done
      name="${url##*/}"; name="${name%.git}"
      printf 'add\t%s\t%s\t%s\t%s\n' "$name" "$url" "$ref" "$sparse" >> "${CODEX_HOME}/.fake-market-ops"
    elif [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "remove" ]; then
      printf 'remove\t%s\n' "${4:-}" >> "${CODEX_HOME}/.fake-market-ops"
    fi
    exit 0
  fi
fi
exit 0
"#,
    )
    .unwrap();
    let mut permissions = fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(path, permissions).unwrap();
}

pub fn fixture() -> (TempDir, PathBuf, PathBuf, PathBuf) {
    let temp = tempfile::tempdir().unwrap();
    let remote = temp.path().join("remote.git");
    let seed = temp.path().join("seed");
    let codex_home = temp.path().join("codex");
    let sync_home = temp.path().join("sync");
    fs::create_dir_all(&codex_home).unwrap();
    run_git(
        temp.path(),
        &[
            "init",
            "--bare",
            "--initial-branch=main",
            remote.to_str().unwrap(),
        ],
    );
    run_git(
        temp.path(),
        &["init", "--initial-branch=main", seed.to_str().unwrap()],
    );
    run_git(&seed, &["config", "user.name", "Seed"]);
    run_git(&seed, &["config", "user.email", "seed@example.test"]);
    fs::create_dir_all(seed.join("config")).unwrap();
    fs::create_dir_all(seed.join("devices")).unwrap();
    fs::create_dir_all(seed.join("agents")).unwrap();
    fs::write(seed.join("codex-sync.toml"), "schema_version = 3\n").unwrap();
    fs::write(seed.join("AGENTS.md"), "# Shared\n").unwrap();
    fs::write(
        seed.join("config/common.toml"),
        "model = \"remote\"\nmodel_reasoning_effort = \"high\"\n",
    )
    .unwrap();
    fs::write(seed.join("devices/test.toml"), "web_search = \"live\"\n").unwrap();
    fs::write(seed.join("marketplaces.toml"), "marketplaces = []\n").unwrap();
    fs::write(seed.join("plugins.toml"), "plugins = []\n").unwrap();
    run_git(&seed, &["add", "."]);
    run_git(&seed, &["commit", "-m", "initial"]);
    run_git(&seed, &["branch", "-M", "main"]);
    run_git(
        &seed,
        &["remote", "add", "origin", remote.to_str().unwrap()],
    );
    run_git(&seed, &["push", "-u", "origin", "HEAD:refs/heads/main"]);
    let codex_bin = temp.path().join("codex-cli");
    fake_codex(&codex_bin);
    (temp, remote, codex_home, sync_home)
}

pub fn command(codex_home: &Path, sync_home: &Path, codex_bin: &Path) -> AssertCommand {
    let mut command = AssertCommand::cargo_bin("codex-sync").unwrap();
    command
        .env("CODEX_HOME", codex_home)
        .env("CODEX_SYNC_HOME", sync_home)
        .env("CODEX_SYNC_CODEX_BIN", codex_bin)
        .env("CODEX_SYNC_ALLOW_LOCAL_REPOSITORY", "1");
    command
}

pub fn fake_market_json(name: &str, url: &str, git_ref: &str, sparse: &str) -> String {
    let sparse = if sparse.is_empty() {
        "[]".to_owned()
    } else {
        format!("[\"{sparse}\"]")
    };
    format!(
        r#"{{"marketplaces":[{{"name":"{name}","marketplaceSource":{{"sourceType":"git","source":"{url}","ref":"{git_ref}","sparse":{sparse}}}}}]}}"#
    )
}

pub fn fake_plugin_json(ids: &[&str]) -> String {
    let installed = ids
        .iter()
        .map(|id| format!(r#"{{"pluginId":"{id}","installed":true,"enabled":true}}"#))
        .collect::<Vec<_>>()
        .join(",");
    format!(r#"{{"installed":[{installed}]}}"#)
}

pub fn write_fake_json(path: &Path, contents: &str) {
    fs::write(path, contents).unwrap();
}
