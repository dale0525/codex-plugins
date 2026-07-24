#![cfg(unix)]

use std::fs;
use std::io::{Cursor, Read, Write};
use std::net::TcpListener;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::thread;

use assert_cmd::Command;
use zip::write::SimpleFileOptions;

fn command(sync_home: &Path, codex_home: &Path, codex_bin: &Path) -> Command {
    let mut command = Command::cargo_bin("codex-sync").unwrap();
    command
        .env("CODEX_SYNC_HOME", sync_home)
        .env("CODEX_HOME", codex_home)
        .env("CODEX_SYNC_CODEX_BIN", codex_bin)
        .env("CODEX_SYNC_GITHUB_TOKEN", "test-token");
    command
}

fn fake_codex(path: &Path) {
    fs::write(
        path,
        r#"#!/usr/bin/env sh
set -eu
if [ -n "${CODEX_SYNC_GITHUB_TOKEN:-}" ]; then
  echo "GitHub token leaked to Codex child" >&2
  exit 9
fi
if [ "${1:-}" = "--version" ]; then
  echo "codex 1.0.0"
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "list" ]; then
  printf 'MARKETPLACE ROOT\n'
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "list" ]; then
  printf '{"installed":[]}'
else
  echo "unexpected codex invocation: $*" >&2
  exit 1
fi
"#,
    )
    .unwrap();
    let mut permissions = fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(path, permissions).unwrap();
}

fn repository_zip(model: Option<&str>) -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let common = model
            .map(|value| format!("model = \"{value}\"\n"))
            .unwrap_or_default();
        let files = [
            (
                "owner-config-commit/codex-sync.toml",
                "schema_version = 1\n",
            ),
            (
                "owner-config-commit/AGENTS.md",
                "# Synchronized instructions\n",
            ),
            ("owner-config-commit/config/common.toml", common.as_str()),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn repository_zip_with_plaintext_provider_token() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            ("owner-config-commit/codex-sync.toml", "schema_version = 1\n"),
            ("owner-config-commit/AGENTS.md", "# Synchronized\n"),
            (
                "owner-config-commit/providers.toml",
                "[providers.company]\nname = \"Company API\"\nbase_url = \"https://api.example.com/v1\"\nwire_api = \"responses\"\nexperimental_bearer_token = \"test-provider-bearer-token\"\n",
            ),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn repository_zip_with_plugins() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            ("owner-config-commit/codex-sync.toml", "schema_version = 1\n"),
            ("owner-config-commit/AGENTS.md", "# New\n"),
            (
                "owner-config-commit/plugins.toml",
                "[[plugins]]\nid = \"good@market\"\nenabled = true\n\n[[plugins]]\nid = \"fail@market\"\nenabled = true\n",
            ),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn repository_zip_with_marketplace_failure() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            ("owner-config-commit/codex-sync.toml", "schema_version = 1\n"),
            ("owner-config-commit/AGENTS.md", "# New\n"),
            (
                "owner-config-commit/marketplaces.toml",
                "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://example.com/new-market.git\"\ngit_ref = \"main\"\n",
            ),
            (
                "owner-config-commit/plugins.toml",
                "[[plugins]]\nid = \"fail@market\"\nenabled = true\n",
            ),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn repository_zip_with_disabled_plugin_failure() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            ("owner-config-commit/codex-sync.toml", "schema_version = 1\n"),
            ("owner-config-commit/AGENTS.md", "# New\n"),
            (
                "owner-config-commit/plugins.toml",
                "[[plugins]]\nid = \"old@market\"\nenabled = false\n\n[[plugins]]\nid = \"fail@market\"\nenabled = true\n",
            ),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn stateful_fake_codex(path: &Path) {
    fs::write(
        path,
        r#"#!/usr/bin/env sh
set -eu
if [ -n "${CODEX_SYNC_GITHUB_TOKEN:-}" ]; then
  echo "GitHub token leaked to Codex child" >&2
  exit 9
fi
state="${FAKE_CODEX_STATE:?}"
if [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "list" ]; then
  printf 'MARKETPLACE ROOT\n'
  if [ -n "${FAKE_MARKETPLACE_STATE:-}" ] && [ -s "$FAKE_MARKETPLACE_STATE" ]; then
    printf '%s %s\n' "${FAKE_MARKETPLACE_NAME:-market}" "$(cat "$FAKE_MARKETPLACE_STATE")"
  fi
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "remove" ]; then
  if [ -n "${FAKE_MARKETPLACE_STATE:-}" ] && [ -s "$FAKE_MARKETPLACE_STATE" ]; then
    root="$(cat "$FAKE_MARKETPLACE_STATE")"
    if [ "${FAKE_DAMAGE_MARKETPLACE_ON_REMOVE:-}" = "1" ] && [ -d "$root" ]; then
      printf 'damaged\n' > "$root/sentinel.txt"
    fi
    : > "$FAKE_MARKETPLACE_STATE"
  fi
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "add" ]; then
  printf '%s' "${4:-}" > "${FAKE_MARKETPLACE_STATE:?}"
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "marketplace" ] && [ "${3:-}" = "upgrade" ]; then
  :
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "list" ]; then
  if [ -s "$state" ]; then
    entry="$(cat "$state")"
    id="${entry%%|*}"
    enabled="${entry#*|}"
    if [ "$enabled" = "$entry" ]; then
      enabled=true
    fi
    printf '{"installed":[{"pluginId":"%s","installed":true,"enabled":%s}]}' "$id" "$enabled"
  else
    printf '{"installed":[]}'
  fi
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "add" ]; then
  if [ "${3:-}" = "fail@market" ]; then
    echo "simulated plugin failure" >&2
    exit 2
  fi
  printf '%s|true' "${3:-}" > "$state"
  if [ -n "${FAKE_CODEX_CONFIG:-}" ]; then
    printf '[plugins."%s"]\nenabled = true\n' "${3:-}" > "$FAKE_CODEX_CONFIG"
  fi
elif [ "${1:-}" = "plugin" ] && [ "${2:-}" = "remove" ]; then
  : > "$state"
elif [ "${1:-}" = "--version" ]; then
  echo "codex 1.0.0"
else
  echo "unexpected codex invocation: $*" >&2
  exit 1
fi
"#,
    )
    .unwrap();
    let mut permissions = fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(path, permissions).unwrap();
}

fn serve_github(commit: &'static str, archive: Vec<u8>) -> (String, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let handle = thread::spawn(move || {
        for expected in ["/commits/main".to_owned(), format!("/zipball/{commit}")] {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 8192];
            let size = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..size]);
            assert!(request.contains(&expected), "unexpected request: {request}");
            if expected.starts_with("/commits") {
                let body = format!(r#"{{"sha":"{commit}"}}"#);
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .unwrap();
                stream.write_all(body.as_bytes()).unwrap();
            } else {
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    archive.len()
                )
                .unwrap();
                stream.write_all(&archive).unwrap();
            }
        }
    });
    (format!("http://{address}"), handle)
}

fn serve_commit(commit: &'static str) -> (String, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let handle = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 8192];
        let size = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..size]).contains("/commits/main"));
        let body = format!(r#"{{"sha":"{commit}"}}"#);
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
    });
    (format!("http://{address}"), handle)
}

#[test]
fn setup_sync_and_apply_preserve_unmanaged_config() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    fs::write(
        codex_home.join("config.toml"),
        "[projects.\"/tmp/example\"]\ntrust_level = \"trusted\"\n",
    )
    .unwrap();
    fs::write(codex_home.join("AGENTS.md"), "# Old\n").unwrap();
    let codex_bin = temporary.path().join("codex-fake");
    fake_codex(&codex_bin);

    command(&sync_home, &codex_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            "owner/config",
            "--device",
            "test-device",
        ])
        .assert()
        .success();
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("Iv23liN2J2Ryzkd99etp"));

    let (api_url, server) = serve_github("abc123", repository_zip(Some("gpt-test")));
    let sync = command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(
        sync.status.success(),
        "{}",
        String::from_utf8_lossy(&sync.stderr)
    );
    server.join().unwrap();
    let stdout = String::from_utf8(sync.stdout).unwrap();
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();

    command(&sync_home, &codex_home, &codex_bin)
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .success();

    let config = fs::read_to_string(codex_home.join("config.toml")).unwrap();
    assert!(config.contains("model = \"gpt-test\""));
    assert!(config.contains("/tmp/example"));
    assert_eq!(
        fs::read_to_string(codex_home.join("AGENTS.md")).unwrap(),
        "# Synchronized instructions\n"
    );

    fs::write(
        sync_home.join("repository/config/common.toml"),
        "model = \"unpublished\"\n",
    )
    .unwrap();
    let (api_url, server) = serve_commit("abc123");
    command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .assert()
        .failure()
        .stderr(predicates::str::contains("unpublished edits"));
    server.join().unwrap();

    let (api_url, server) = serve_github("abc123", repository_zip(Some("gpt-test")));
    command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .args(["sync", "--discard-local"])
        .assert()
        .success();
    server.join().unwrap();

    let (api_url, server) = serve_github("def456", repository_zip(None));
    let removal = command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(
        removal.status.success(),
        "{}",
        String::from_utf8_lossy(&removal.stderr)
    );
    server.join().unwrap();
    let stdout = String::from_utf8(removal.stdout).unwrap();
    assert!(stdout.contains("remove previously synchronized value"));
    let removal_plan = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();
    command(&sync_home, &codex_home, &codex_bin)
        .args(["apply", removal_plan])
        .assert()
        .success();
    let config = fs::read_to_string(codex_home.join("config.toml")).unwrap();
    assert!(!config.contains("model ="));
    assert!(config.contains("/tmp/example"));
}

#[test]
fn plaintext_provider_token_is_applied_without_leaking_into_plan_output() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    let codex_bin = temporary.path().join("codex-fake");
    fake_codex(&codex_bin);

    command(&sync_home, &codex_home, &codex_bin)
        .args([
            "setup",
            "--repository",
            "owner/config",
            "--device",
            "test-device",
        ])
        .assert()
        .success();
    let (api_url, server) = serve_github("abc123", repository_zip_with_plaintext_provider_token());
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    server.join().unwrap();
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("model_providers.company.experimental_bearer_token"));
    assert!(!stdout.contains("test-provider-bearer-token"));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("test-provider-bearer-token"));
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();

    command(&sync_home, &codex_home, &codex_bin)
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .success();
    assert!(fs::read_to_string(codex_home.join("config.toml"))
        .unwrap()
        .contains("experimental_bearer_token = \"test-provider-bearer-token\""));
}

#[test]
fn failed_plugin_transaction_restores_files_and_plugin_state() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    fs::write(codex_home.join("config.toml"), "model = \"old\"\n").unwrap();
    fs::write(codex_home.join("AGENTS.md"), "# Old\n").unwrap();
    let codex_bin = temporary.path().join("codex-stateful");
    let plugin_state = temporary.path().join("plugin-state");
    fs::write(&plugin_state, "").unwrap();
    stateful_fake_codex(&codex_bin);

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .args([
            "setup",
            "--repository",
            "owner/config",
            "--device",
            "test-device",
        ])
        .assert()
        .success();
    let (api_url, server) = serve_github("abc123", repository_zip_with_plugins());
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(output.status.success());
    server.join().unwrap();
    let stdout = String::from_utf8(output.stdout).unwrap();
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("restored the pre-apply backup"));

    assert_eq!(fs::read_to_string(plugin_state).unwrap(), "");
    assert_eq!(
        fs::read_to_string(codex_home.join("config.toml")).unwrap(),
        "model = \"old\"\n"
    );
    assert_eq!(
        fs::read_to_string(codex_home.join("AGENTS.md")).unwrap(),
        "# Old\n"
    );
}

#[test]
fn failed_transaction_restores_marketplace_registration_and_snapshot() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    fs::write(codex_home.join("config.toml"), "model = \"old\"\n").unwrap();
    fs::write(codex_home.join("AGENTS.md"), "# Old\n").unwrap();

    let old_marketplace = sync_home.join("marketplaces/market/old-commit");
    fs::create_dir_all(old_marketplace.join(".agents/plugins")).unwrap();
    fs::write(
        old_marketplace.join(".agents/plugins/marketplace.json"),
        r#"{"name":"market","plugins":[]}"#,
    )
    .unwrap();
    fs::write(old_marketplace.join("sentinel.txt"), "original\n").unwrap();

    let codex_bin = temporary.path().join("codex-stateful");
    let plugin_state = temporary.path().join("plugin-state");
    let marketplace_state = temporary.path().join("marketplace-state");
    fs::write(&plugin_state, "").unwrap();
    fs::write(
        &marketplace_state,
        old_marketplace.to_string_lossy().as_bytes(),
    )
    .unwrap();
    stateful_fake_codex(&codex_bin);

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
        .args([
            "setup",
            "--repository",
            "owner/config",
            "--device",
            "test-device",
        ])
        .assert()
        .success();
    let (api_url, server) = serve_github("abc123", repository_zip_with_marketplace_failure());
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    server.join().unwrap();
    let stdout = String::from_utf8(output.stdout).unwrap();
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
        .env("FAKE_DAMAGE_MARKETPLACE_ON_REMOVE", "1")
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("restored the pre-apply backup"));

    assert_eq!(
        fs::read_to_string(&marketplace_state).unwrap(),
        old_marketplace.to_string_lossy()
    );
    assert_eq!(
        fs::read_to_string(old_marketplace.join("sentinel.txt")).unwrap(),
        "original\n"
    );
}

#[test]
fn rollback_reinstalls_disabled_plugin_then_restores_disabled_config() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    let original_config = "[plugins.\"old@market\"]\nenabled = false\n";
    fs::write(codex_home.join("config.toml"), original_config).unwrap();
    fs::write(codex_home.join("AGENTS.md"), "# Old\n").unwrap();
    let codex_bin = temporary.path().join("codex-stateful");
    let plugin_state = temporary.path().join("plugin-state");
    fs::write(&plugin_state, "old@market|false").unwrap();
    stateful_fake_codex(&codex_bin);

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_CODEX_CONFIG", codex_home.join("config.toml"))
        .args([
            "setup",
            "--repository",
            "owner/config",
            "--device",
            "test-device",
        ])
        .assert()
        .success();
    let (api_url, server) = serve_github("abc123", repository_zip_with_disabled_plugin_failure());
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_CODEX_CONFIG", codex_home.join("config.toml"))
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    server.join().unwrap();
    let stdout = String::from_utf8(output.stdout).unwrap();
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_CODEX_CONFIG", codex_home.join("config.toml"))
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("restored the pre-apply backup"));

    assert_eq!(
        fs::read_to_string(&plugin_state).unwrap(),
        "old@market|true"
    );
    assert_eq!(
        fs::read_to_string(codex_home.join("config.toml")).unwrap(),
        original_config
    );
}
