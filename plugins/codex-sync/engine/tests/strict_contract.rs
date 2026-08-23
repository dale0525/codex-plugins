#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use assert_cmd::Command as AssertCommand;
use tempfile::TempDir;

fn run_git(cwd: &Path, args: &[&str]) {
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

fn fake_codex(path: &Path) {
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
    case "${3:-}" in add|remove|upgrade) mutation=1 ;; esac
  fi
  if [ "$mutation" -eq 1 ]; then
    if [ -n "${FAKE_CODEX_LOG:-}" ]; then echo "$*" >> "$FAKE_CODEX_LOG"; fi
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

fn fixture() -> (TempDir, PathBuf, PathBuf, PathBuf) {
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
        &["init", "--initial-branch=main", &seed.to_string_lossy()],
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

fn command(codex_home: &Path, sync_home: &Path, codex_bin: &Path) -> AssertCommand {
    let mut command = AssertCommand::cargo_bin("codex-sync").unwrap();
    command
        .env("CODEX_HOME", codex_home)
        .env("CODEX_SYNC_HOME", sync_home)
        .env("CODEX_SYNC_CODEX_BIN", codex_bin)
        .env("CODEX_SYNC_ALLOW_LOCAL_REPOSITORY", "1");
    command
}

fn market_json(url: &str) -> String {
    format!(
        r#"{{"marketplaces":[{{"name":"market","marketplaceSource":{{"sourceType":"git","source":"{url}","ref":"main","sparse":[]}}}}]}}"#
    )
}

fn plugin_json(ids: &[&str]) -> String {
    let items = ids
        .iter()
        .map(|id| format!(r#"{{"pluginId":"{id}","installed":true,"enabled":true}}"#))
        .collect::<Vec<_>>()
        .join(",");
    format!(r#"{{"installed":[{items}]}}"#)
}

#[test]
fn setup_rejects_ssh_password_before_git_without_leaking_url() {
    let (temp, _remote, codex_home, sync_home) = fixture();
    fs::create_dir_all(&sync_home).unwrap();
    fs::write(
        sync_home.join("state.toml"),
        "schema_version = 3\nrepository = \"ssh://user:secret@example.test/repo.git\"\nbranch = \"main\"\ndevice = \"test\"\n",
    )
    .unwrap();
    let wrapper_dir = temp.path().join("git-wrapper");
    fs::create_dir_all(&wrapper_dir).unwrap();
    let wrapper = wrapper_dir.join("git");
    fs::write(
        &wrapper,
        "#!/bin/sh\necho \"$*\" >> \"$GIT_LOG\"\nexec \"$REAL_GIT\" \"$@\"\n",
    )
    .unwrap();
    let mut permissions = fs::metadata(&wrapper).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&wrapper, permissions).unwrap();
    let real_git = Command::new("sh")
        .args(["-c", "command -v git"])
        .output()
        .unwrap();
    let real_git = String::from_utf8(real_git.stdout)
        .unwrap()
        .trim()
        .to_owned();
    let path = format!(
        "{}:{}",
        wrapper_dir.display(),
        std::env::var("PATH").unwrap_or_default()
    );
    let log = temp.path().join("git.log");
    let output = command(&codex_home, &sync_home, &temp.path().join("codex-cli"))
        .env("PATH", path)
        .env("REAL_GIT", real_git)
        .env("GIT_LOG", &log)
        .args(["setup"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("password"));
    assert!(!stderr.contains("secret"));
    assert!(!stderr.contains("ssh://"));
    assert!(!log.exists() || fs::read_to_string(log).unwrap().is_empty());
}

#[test]
fn legacy_ownership_fields_are_dropped_on_next_state_save() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let codex_bin = temp.path().join("codex-cli");
    command(&codex_home, &sync_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();
    let state_path = sync_home.join("state.toml");
    let mut state = fs::read_to_string(&state_path).unwrap();
    state.push_str("managed_markets = [\"old\"]\nmanaged_plugins = [\"old@market\"]\n");
    fs::write(&state_path, state).unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["setup"])
        .assert()
        .success();
    let saved = fs::read_to_string(state_path).unwrap();
    assert!(!saved.contains("managed_markets"));
    assert!(!saved.contains("managed_plugins"));
}

#[test]
fn missing_remote_market_for_plugin_fails_preflight_without_mutation() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("missing-market-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("plugins.toml"),
        "plugins = [\"missing@market\"]\n",
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "missing market plugin"]);
    run_git(&edit, &["push", "origin", "main"]);
    let codex_bin = temp.path().join("codex-cli");
    command(&codex_home, &sync_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();
    let log = temp.path().join("missing-actions.log");
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_LOG", &log)
        .args(["pull"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("preflight"));
    assert!(!log.exists());
    let state = fs::read_to_string(sync_home.join("state.toml")).unwrap();
    assert!(state.contains("converged = false"));
    assert!(
        !state.contains("last_applied_commit = \"") || state.contains("last_applied_commit = \"\"")
    );
}

#[test]
fn real_no_change_push_records_base_commit_and_converged_state() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let codex_bin = temp.path().join("codex-cli");
    command(&codex_home, &sync_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    let expected = Command::new("git")
        .args(["--git-dir", remote.to_str().unwrap(), "rev-parse", "main"])
        .output()
        .unwrap();
    let expected = String::from_utf8_lossy(&expected.stdout).trim().to_owned();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let state = fs::read_to_string(sync_home.join("state.toml")).unwrap();
    assert!(state.contains(&format!("last_applied_commit = \"{expected}\"")));
    assert!(state.contains("converged = true"));
}

#[test]
fn source_mismatch_replaces_market_and_remote_deletion_removes_it() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("conflict-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://new.test/market.git\"\n",
    )
    .unwrap();
    fs::write(
        edit.join("plugins.toml"),
        "plugins = [\"desired@market\"]\n",
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "conflicting desired market"]);
    run_git(&edit, &["push", "origin", "main"]);

    let markets = temp.path().join("markets.json");
    let plugins = temp.path().join("plugins.json");
    fs::write(&markets, market_json("https://old.test/market.git")).unwrap();
    fs::write(&plugins, plugin_json(&["existing@market"])).unwrap();
    let codex_bin = temp.path().join("codex-cli");
    command(&codex_home, &sync_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();
    let log = temp.path().join("actions.log");
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins)
        .env("FAKE_CODEX_LOG", &log)
        .args(["pull"])
        .assert()
        .success();
    let first_log = fs::read_to_string(&log).unwrap();
    let lines = first_log.lines().collect::<Vec<_>>();
    assert!(
        lines
            .iter()
            .position(|line| line == &"plugin remove existing@market")
            .unwrap()
            < lines
                .iter()
                .position(|line| line == &"plugin marketplace remove market")
                .unwrap()
    );
    assert!(lines
        .iter()
        .any(|line| line.starts_with("plugin marketplace add https://new.test/market.git")));
    assert!(lines
        .iter()
        .any(|line| line == &"plugin add desired@market"));
    assert!(!fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("managed_markets"));

    fs::write(edit.join("marketplaces.toml"), "marketplaces = []\n").unwrap();
    fs::write(edit.join("plugins.toml"), "plugins = []\n").unwrap();
    run_git(&edit, &["add", "."]);
    run_git(
        &edit,
        &["commit", "-m", "remove conflicting desired market"],
    );
    run_git(&edit, &["push", "origin", "main"]);
    let _ = fs::remove_file(&log);
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins)
        .env("FAKE_CODEX_LOG", &log)
        .args(["pull"])
        .assert()
        .success();
    let second_log = fs::read_to_string(&log).unwrap();
    assert!(second_log.contains("plugin remove desired@market"));
    assert!(second_log.contains("plugin marketplace remove market"));
    assert!(!fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("managed_markets"));
}

#[test]
fn pull_removes_config_only_plugin_omitted_by_codex_listing() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let markets = temp.path().join("markets.json");
    let plugins = temp.path().join("plugins.json");
    fs::write(
        &markets,
        r#"{"marketplaces":[{"name":"market","marketplaceSource":{"sourceType":"git","source":"https://example.test/market.git","ref":"main","sparse":[]}},{"name":"local-market","marketplaceSource":{"sourceType":"local","source":"/tmp/local-market"}}]}"#,
    )
    .unwrap();
    fs::write(&plugins, plugin_json(&[])).unwrap();
    fs::write(
        codex_home.join("config.toml"),
        "custom_local = \"keep\"\n\n[plugins.\"stale@market\"]\nenabled = true\n\n[plugins.\"local@local-market\"]\nenabled = true\n\n[plugins.\"browser@openai-bundled\"]\nenabled = true\n",
    )
    .unwrap();
    let codex_bin = temp.path().join("codex-cli");
    command(&codex_home, &sync_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();

    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins)
        .args(["pull", "--dry-run"])
        .assert()
        .success()
        .stdout(predicates::str::contains("remove plugin stale@market"));
    assert!(fs::read_to_string(codex_home.join("config.toml"))
        .unwrap()
        .contains("stale@market"));

    let log = temp.path().join("actions.log");
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins)
        .env("FAKE_CODEX_LOG", &log)
        .args(["pull"])
        .assert()
        .success();
    let config = fs::read_to_string(codex_home.join("config.toml")).unwrap();
    assert!(!config.contains("stale@market"));
    assert!(config.contains("custom_local = \"keep\""));
    assert!(config.contains("local@local-market"));
    assert!(config.contains("browser@openai-bundled"));
    assert!(fs::read_to_string(log)
        .unwrap()
        .contains("plugin marketplace remove market"));
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("converged = true"));
}

fn seed_v2(remote: &Path, temp: &Path) -> PathBuf {
    let edit = temp.join("v2-edit");
    run_git(
        temp,
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(edit.join("codex-sync.toml"), "schema_version = 2\n").unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "v2 remote"]);
    run_git(&edit, &["push", "origin", "main"]);
    edit
}

#[test]
fn setup_migrates_only_cache_and_push_uses_latest_v2_commit() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = seed_v2(&remote, temp.path());
    let codex_bin = temp.path().join("codex-cli");
    command(&codex_home, &sync_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();
    assert_eq!(
        fs::read_to_string(sync_home.join("git-cache/codex-sync.toml"))
            .unwrap()
            .trim(),
        "schema_version = 3"
    );
    let remote_manifest = Command::new("git")
        .args([
            "--git-dir",
            remote.to_str().unwrap(),
            "show",
            "main:codex-sync.toml",
        ])
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&remote_manifest.stdout).trim(),
        "schema_version = 2"
    );
    let state = fs::read_to_string(sync_home.join("state.toml")).unwrap();
    assert!(state.contains("migration_cleanup_pending = true"));
    assert!(state.contains("converged = false"));
    assert!(!state.contains("migration_pushed_commit = \""));

    for _ in 0..2 {
        command(&codex_home, &sync_home, &codex_bin)
            .args(["pull"])
            .assert()
            .success();
        assert!(!codex_home.join("config.toml").exists());
        assert!(fs::read_to_string(sync_home.join("state.toml"))
            .unwrap()
            .contains("migration_cleanup_pending = true"));
    }
    fs::write(edit.join("concurrent-v2.txt"), "concurrent\n").unwrap();
    run_git(&edit, &["add", "concurrent-v2.txt"]);
    run_git(&edit, &["commit", "-m", "concurrent v2 update"]);
    run_git(&edit, &["push", "origin", "main"]);
    fs::write(
        codex_home.join("config.toml"),
        "model = \"migrated\"\nmodel_reasoning_effort = \"high\"\n",
    )
    .unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let check = temp.path().join("v2-check");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), check.to_str().unwrap()],
    );
    assert!(check.join("concurrent-v2.txt").exists());
    assert_eq!(
        fs::read_to_string(check.join("codex-sync.toml"))
            .unwrap()
            .trim(),
        "schema_version = 3"
    );
}

#[test]
fn remote_v3_noop_push_records_migration_evidence_then_cleanup() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = seed_v2(&remote, temp.path());
    let codex_bin = temp.path().join("codex-cli");
    command(&codex_home, &sync_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();
    fs::write(edit.join("codex-sync.toml"), "schema_version = 3\n").unwrap();
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "other device migration"]);
    run_git(&edit, &["push", "origin", "main"]);
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    fs::create_dir_all(sync_home.join("backups")).unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push", "--dry-run"])
        .assert()
        .success();
    assert!(!fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("migration_pushed_commit = \""));
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let state = fs::read_to_string(sync_home.join("state.toml")).unwrap();
    assert!(state.contains("migration_cleanup_pending = true"));
    assert!(state.contains("migration_pushed_commit = \""));
    let old_commit = state
        .lines()
        .find(|line| line.starts_with("last_applied_commit = \""))
        .unwrap()
        .to_owned();
    let locked = sync_home.join("backups");
    fs::create_dir_all(locked.join("locked")).unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("clean up legacy sync data"));
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
    let failed_state = fs::read_to_string(sync_home.join("state.toml")).unwrap();
    assert!(failed_state.contains(&old_commit));
    assert!(failed_state.contains("converged = false"));
    assert!(failed_state.contains("migration_cleanup_pending = true"));
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    assert!(!sync_home.join("backups").exists());
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("migration_cleanup_pending = false"));
}
