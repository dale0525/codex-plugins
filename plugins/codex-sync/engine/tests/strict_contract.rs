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
if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "list" ] && [ -n "${FAKE_CODEX_PLUGINS_JSON:-}" ]; then cat "$FAKE_CODEX_PLUGINS_JSON"; exit 0; fi
if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "list" ] && [ -n "${FAKE_CODEX_MARKETS_JSON:-}" ]; then cat "$FAKE_CODEX_MARKETS_JSON"; exit 0; fi
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
    run_git(temp.path(), &["init", "--bare", remote.to_str().unwrap()]);
    run_git(temp.path(), &["init", &seed.to_string_lossy()]);
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
    run_git(&seed, &["push", "-u", "origin", "main"]);
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
fn conflicted_unmanaged_market_is_preserved_across_removal() {
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
    assert!(!log.exists() || fs::read_to_string(&log).unwrap().is_empty());
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("managed_markets = []"));

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
    assert!(!log.exists() || fs::read_to_string(&log).unwrap().is_empty());
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("managed_markets = []"));
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
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    assert!(!sync_home.join("backups").exists());
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("migration_cleanup_pending = false"));
}
