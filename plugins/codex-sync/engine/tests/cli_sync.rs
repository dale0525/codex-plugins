#![cfg(unix)]

use std::fs;
use std::io::{Cursor, Read, Write};
use std::net::TcpListener;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::thread;

use assert_cmd::Command;
use zip::write::SimpleFileOptions;

const DEFAULT_PROFILE: &str = "name = \"default\"\ndescription = \"General-purpose scout\"\nmodel = \"gpt-test\"\nmodel_reasoning_effort = \"medium\"\ndeveloper_instructions = \"Return compact evidence.\"\n\n[features]\nimage_generation = false\n";
const UPDATED_DEFAULT_PROFILE: &str = "name = \"default\"\ndescription = \"Updated general-purpose scout\"\nmodel = \"gpt-test\"\nmodel_reasoning_effort = \"high\"\ndeveloper_instructions = \"Return updated compact evidence.\"\n\n[features]\nimage_generation = false\n";
const IMAGE_PROFILE: &str = "name = \"image\"\ndescription = \"Image specialist\"\nmodel = \"gpt-test\"\nmodel_reasoning_effort = \"max\"\ndeveloper_instructions = \"Handle raster image work.\"\n\n[features]\nimage_generation = true\n";

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
  if [ -n "${FAKE_CODEX_INSTALLED_JSON:-}" ]; then
    cat "$FAKE_CODEX_INSTALLED_JSON"
  else
    printf '{"installed":[]}'
  fi
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
                "schema_version = 2\n",
            ),
            (
                "owner-config-commit/AGENTS.md",
                "# Synchronized instructions\n",
            ),
            ("owner-config-commit/config/common.toml", common.as_str()),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn repository_zip_with_profiles(profiles: &[(&str, &str)]) -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        for (path, content) in [
            (
                "owner-config-commit/codex-sync.toml",
                "schema_version = 2\n",
            ),
            (
                "owner-config-commit/AGENTS.md",
                "# Synchronized instructions\n",
            ),
        ] {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        for (name, content) in profiles {
            zip.start_file(format!("owner-config-commit/agents/{name}.toml"), options)
                .unwrap();
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
            ("owner-config-commit/codex-sync.toml", "schema_version = 2\n"),
            ("owner-config-commit/AGENTS.md", "# Synchronized\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
            (
                "owner-config-commit/providers.toml",
                "[providers.company]\nname = \"Company API\"\nbase_url = \"https://api.example.com/v1\"\nwire_api = \"responses\"\nenv_key = \"COMPANY_OPENAI_API_KEY\"\nexperimental_bearer_token = \"test-provider-bearer-token\"\n",
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
            ("owner-config-commit/codex-sync.toml", "schema_version = 2\n"),
            ("owner-config-commit/AGENTS.md", "# New\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
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

fn repository_zip_with_external_agents() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            (
                "owner-config-commit/codex-sync.toml",
                "schema_version = 2\n\n[[external_agents_sections]]\nid = \"fastctx\"\nbegin_marker = \"<!-- fastctx:begin -->\"\nend_marker = \"<!-- fastctx:end -->\"\n",
            ),
            ("owner-config-commit/AGENTS.md", "# Canonical\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn repository_zip_with_auto_provisioned_plugin() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            ("owner-config-commit/codex-sync.toml", "schema_version = 2\n"),
            ("owner-config-commit/AGENTS.md", "# Canonical\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
            (
                "owner-config-commit/marketplaces.toml",
                "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://example.com/market.git\"\ngit_ref = \"main\"\n",
            ),
            (
                "owner-config-commit/plugins.toml",
                "[[plugins]]\nid = \"fastctx@market\"\nenabled = true\nauto_provision = true\n",
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

fn repository_zip_with_managed_marketplace_and_no_plugins() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            ("owner-config-commit/codex-sync.toml", "schema_version = 2\n"),
            ("owner-config-commit/AGENTS.md", "# New\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
            (
                "owner-config-commit/marketplaces.toml",
                "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://example.com/market.git\"\ngit_ref = \"main\"\n",
            ),
            ("owner-config-commit/plugins.toml", ""),
        ];
        for (path, content) in files {
            zip.start_file(path, options).unwrap();
            zip.write_all(content.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
    }
    cursor.into_inner()
}

fn repository_zip_for_capture() -> Vec<u8> {
    let mut cursor = Cursor::new(Vec::new());
    {
        let mut zip = zip::ZipWriter::new(&mut cursor);
        let options = SimpleFileOptions::default();
        let files = [
            ("owner-config-commit/codex-sync.toml", "schema_version = 2\n"),
            ("owner-config-commit/AGENTS.md", "# Remote instructions\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
            (
                "owner-config-commit/config/common.toml",
                "model = \"remote-model\"\nmodel_reasoning_effort = \"low\"\n",
            ),
            (
                "owner-config-commit/devices/test-device.toml",
                "model = \"device-model\"\nweb_search = \"cached\"\n",
            ),
            (
                "owner-config-commit/providers.toml",
                "[providers.cpa]\nname = \"Old\"\nbase_url = \"https://old.example/v1\"\n",
            ),
            (
                "owner-config-commit/marketplaces.toml",
                "[[marketplaces]]\nsource = \"git\"\nname = \"private-market\"\nurl = \"https://example.com/private.git\"\ngit_ref = \"main\"\nsparse = []\n",
            ),
            (
                "owner-config-commit/plugins.toml",
                "[[plugins]]\nid = \"existing@private-market\"\nenabled = true\n\n[[plugins]]\nid = \"missing@private-market\"\nenabled = true\n\n[[plugins]]\nid = \"legacy-disabled@private-market\"\nenabled = false\n\n[[plugins]]\nid = \"documents@openai-primary-runtime\"\nenabled = true\n",
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
            ("owner-config-commit/codex-sync.toml", "schema_version = 2\n"),
            ("owner-config-commit/AGENTS.md", "# New\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
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
            ("owner-config-commit/codex-sync.toml", "schema_version = 2\n"),
            ("owner-config-commit/AGENTS.md", "# New\n"),
            ("owner-config-commit/agents/default.toml", DEFAULT_PROFILE),
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
  if [ "${4:-}" = "--json" ]; then
    if [ -n "${FAKE_MARKETPLACE_STATE:-}" ] && [ -s "$FAKE_MARKETPLACE_STATE" ]; then
      printf '{"marketplaces":[{"name":"%s","root":"%s","marketplaceSource":{"sourceType":"git","source":"%s"}}]}' \
        "${FAKE_MARKETPLACE_NAME:-market}" \
        "$(cat "$FAKE_MARKETPLACE_STATE")" \
        "${FAKE_MARKETPLACE_SOURCE:-https://example.com/market.git}"
    else
      printf '{"marketplaces":[]}'
    fi
  else
    printf 'MARKETPLACE ROOT\n'
    if [ -n "${FAKE_MARKETPLACE_STATE:-}" ] && [ -s "$FAKE_MARKETPLACE_STATE" ]; then
      printf '%s %s\n' "${FAKE_MARKETPLACE_NAME:-market}" "$(cat "$FAKE_MARKETPLACE_STATE")"
    fi
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
  if [ -n "${FAKE_REMOVE_CWD_ON_UPGRADE:-}" ]; then
    rm -rf -- "$FAKE_REMOVE_CWD_ON_UPGRADE"
  fi
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
    command(&sync_home, &codex_home, &codex_bin)
        .arg("doctor")
        .assert()
        .success()
        .stdout(predicates::str::contains(format!(
            "Codex CLI: {}",
            codex_bin.display()
        )));

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
fn sync_apply_and_capture_preserve_external_agents_ownership() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    let marker = "<!-- fastctx:begin -->\nFastCtx live\n<!-- fastctx:end -->";
    fs::write(
        codex_home.join("AGENTS.md"),
        format!("# Local\n\n{marker}\n"),
    )
    .unwrap();
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
    let (api_url, server) = serve_github("abc123", repository_zip_with_external_agents());
    let sync = command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    server.join().unwrap();
    assert!(sync.status.success());
    let output = String::from_utf8(sync.stdout).unwrap();
    let plan_id = output
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();
    command(&sync_home, &codex_home, &codex_bin)
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .success();
    assert_eq!(
        fs::read_to_string(codex_home.join("AGENTS.md")).unwrap(),
        format!("# Canonical\n\n{marker}\n")
    );

    fs::write(
        codex_home.join("AGENTS.md"),
        format!("# Captured base\n\n{marker}\n"),
    )
    .unwrap();
    command(&sync_home, &codex_home, &codex_bin)
        .arg("capture")
        .assert()
        .success();
    assert_eq!(
        fs::read_to_string(sync_home.join("repository/AGENTS.md")).unwrap(),
        "# Captured base\n"
    );
}

#[test]
fn apply_installs_then_runs_reviewed_plugin_provisioner() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    fs::write(codex_home.join("AGENTS.md"), "# Local\n").unwrap();
    let codex_bin = temporary.path().join("codex-fake");
    stateful_fake_codex(&codex_bin);
    let codex_state = temporary.path().join("codex-state");
    fs::write(&codex_state, "").unwrap();
    let marketplace_root = temporary.path().join("marketplace");
    let plugin_root = marketplace_root.join("plugins/fastctx");
    fs::create_dir_all(plugin_root.join(".codex-sync")).unwrap();
    fs::create_dir_all(plugin_root.join("scripts")).unwrap();
    fs::create_dir_all(marketplace_root.join(".agents/plugins")).unwrap();
    fs::write(
        marketplace_root.join(".agents/plugins/marketplace.json"),
        r#"{"plugins":[{"name":"fastctx","source":{"source":"local","path":"./plugins/fastctx"}}]}"#,
    )
    .unwrap();
    fs::write(
        plugin_root.join(".codex-sync/provision.json"),
        r#"{"schema_version":1,"risk":"high","posix_script":"./scripts/provision.sh","windows_script":"./scripts/provision.ps1","arguments":["setup","--yes"]}"#,
    )
    .unwrap();
    let provision_script = plugin_root.join("scripts/provision.sh");
    fs::write(
        &provision_script,
        "#!/usr/bin/env sh\nset -eu\n[ -z \"${CODEX_SYNC_GITHUB_TOKEN:-}\" ]\n[ -z \"${GITHUB_TOKEN:-}\" ]\n[ -z \"${GH_TOKEN:-}\" ]\nprintf provisioned > \"${PROVISION_SENTINEL:?}\"\n",
    )
    .unwrap();
    let mut permissions = fs::metadata(&provision_script).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&provision_script, permissions).unwrap();
    let marketplace_state = temporary.path().join("marketplace-state");
    fs::write(
        &marketplace_state,
        marketplace_root.to_string_lossy().as_bytes(),
    )
    .unwrap();
    let sentinel = temporary.path().join("provisioned");

    let configured = |command: &mut Command| {
        command
            .env("FAKE_CODEX_STATE", &codex_state)
            .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
            .env("FAKE_MARKETPLACE_NAME", "market")
            .env("FAKE_MARKETPLACE_SOURCE", "https://example.com/market.git")
            .env("GITHUB_TOKEN", "test-github-token")
            .env("GH_TOKEN", "test-gh-token")
            .env("PROVISION_SENTINEL", &sentinel);
    };
    let mut setup = command(&sync_home, &codex_home, &codex_bin);
    configured(&mut setup);
    setup
        .args([
            "setup",
            "--repository",
            "owner/config",
            "--device",
            "test-device",
        ])
        .assert()
        .success();
    let (api_url, server) = serve_github("abc123", repository_zip_with_auto_provisioned_plugin());
    let mut sync = command(&sync_home, &codex_home, &codex_bin);
    configured(&mut sync);
    let output = sync
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    server.join().unwrap();
    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("plugin-provision"));
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();
    let mut apply = command(&sync_home, &codex_home, &codex_bin);
    configured(&mut apply);
    apply
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .success()
        .stdout(predicates::str::contains("provisioned fastctx@market"));
    assert_eq!(fs::read_to_string(&sentinel).unwrap(), "provisioned");
    assert!(fs::read_to_string(&codex_state)
        .unwrap()
        .starts_with("fastctx@market|true"));
}

#[test]
fn capture_updates_managed_state_and_excludes_openai_plugins() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(codex_home.join("agents")).unwrap();
    fs::write(codex_home.join("AGENTS.md"), "# Local instructions\n").unwrap();
    fs::write(
        codex_home.join("agents/default.toml"),
        UPDATED_DEFAULT_PROFILE,
    )
    .unwrap();
    fs::write(
        codex_home.join("config.toml"),
        r#"model = "local-model"
model_reasoning_effort = "high"
web_search = "live"

[model_providers.cpa]
name = "New CPA"
base_url = "https://new.example/v1"
wire_api = "responses"
experimental_bearer_token = "test-captured-token"

[marketplaces.new-private]
source_type = "git"
source = "https://example.com/new-private.git"
ref = "stable"
"#,
    )
    .unwrap();
    let installed = temporary.path().join("installed.json");
    fs::write(
        &installed,
        r#"{"installed":[
{"pluginId":"existing@private-market","installed":true,"enabled":true},
{"pluginId":"new-tool@new-private","installed":true,"enabled":true},
{"pluginId":"documents@openai-primary-runtime","installed":true,"enabled":true},
{"pluginId":"browser@openai-bundled","installed":true,"enabled":true},
{"pluginId":"local-tool@personal","installed":true,"enabled":true}
]}"#,
    )
    .unwrap();
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
    let (api_url, server) = serve_github("abc123", repository_zip_for_capture());
    command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .assert()
        .success();
    server.join().unwrap();

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_INSTALLED_JSON", &installed)
        .arg("capture")
        .assert()
        .success()
        .stdout(predicates::str::contains(
            "captured 2 installed non-OpenAI plugin(s)",
        ))
        .stdout(predicates::str::contains(
            "excluded 2 OpenAI-managed plugin(s)",
        ))
        .stdout(predicates::str::contains("skipped local-tool@personal"));

    let repository = sync_home.join("repository");
    let common = fs::read_to_string(repository.join("config/common.toml")).unwrap();
    assert!(common.contains("model = \"remote-model\""));
    assert!(common.contains("model_reasoning_effort = \"high\""));
    let device = fs::read_to_string(repository.join("devices/test-device.toml")).unwrap();
    assert!(device.contains("model = \"local-model\""));
    assert!(device.contains("web_search = \"live\""));
    let providers = fs::read_to_string(repository.join("providers.toml")).unwrap();
    assert!(providers.contains("name = \"New CPA\""));
    assert!(providers.contains("experimental_bearer_token = \"test-captured-token\""));
    assert_eq!(
        fs::read_to_string(repository.join("AGENTS.md")).unwrap(),
        "# Local instructions\n"
    );
    assert_eq!(
        fs::read_to_string(repository.join("agents/default.toml")).unwrap(),
        UPDATED_DEFAULT_PROFILE
    );
    let plugins = fs::read_to_string(repository.join("plugins.toml")).unwrap();
    assert!(plugins.contains("existing@private-market"));
    assert!(plugins.contains("new-tool@new-private"));
    assert!(!plugins.contains("missing@private-market"));
    assert!(!plugins.contains("legacy-disabled@private-market"));
    assert!(!plugins.contains("enabled = false"));
    assert!(!plugins.contains("openai-"));
    assert!(!plugins.contains("local-tool@personal"));
    let marketplaces = fs::read_to_string(repository.join("marketplaces.toml")).unwrap();
    assert!(marketplaces.contains("name = \"new-private\""));
    assert!(marketplaces.contains("git_ref = \"stable\""));

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_INSTALLED_JSON", &installed)
        .arg("capture")
        .assert()
        .failure()
        .stderr(predicates::str::contains("unpublished edits"));
}

#[test]
fn agent_profiles_are_transactional_and_preserve_unmanaged_profiles() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    let agents = codex_home.join("agents");
    fs::create_dir_all(&agents).unwrap();
    fs::write(agents.join("personal.toml"), "personal\n").unwrap();
    fs::write(agents.join("default.toml"), "local drift\n").unwrap();
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

    let initial_archive =
        repository_zip_with_profiles(&[("default", DEFAULT_PROFILE), ("image", IMAGE_PROFILE)]);
    let (api_url, server) = serve_github("abc123", initial_archive);
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(output.status.success());
    server.join().unwrap();
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("agent-profile default.toml"));
    assert!(stdout.contains("agent-profile image.toml"));
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();

    fs::write(agents.join("default.toml"), "changed after planning\n").unwrap();
    command(&sync_home, &codex_home, &codex_bin)
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .failure()
        .stderr(predicates::str::contains(
            "Codex configuration changed after planning",
        ));

    let (api_url, server) = serve_commit("abc123");
    let output = command(&sync_home, &codex_home, &codex_bin)
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
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .success();
    assert_eq!(
        fs::read_to_string(agents.join("default.toml")).unwrap(),
        DEFAULT_PROFILE
    );
    assert_eq!(
        fs::read_to_string(agents.join("image.toml")).unwrap(),
        IMAGE_PROFILE
    );
    assert_eq!(
        fs::read_to_string(agents.join("personal.toml")).unwrap(),
        "personal\n"
    );

    let updated_archive = repository_zip_with_profiles(&[("default", UPDATED_DEFAULT_PROFILE)]);
    let (api_url, server) = serve_github("def456", updated_archive);
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(output.status.success());
    server.join().unwrap();
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("remove previously synchronized agent profile"));
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();
    command(&sync_home, &codex_home, &codex_bin)
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .success();
    assert_eq!(
        fs::read_to_string(agents.join("default.toml")).unwrap(),
        UPDATED_DEFAULT_PROFILE
    );
    assert!(!agents.join("image.toml").exists());
    assert_eq!(
        fs::read_to_string(agents.join("personal.toml")).unwrap(),
        "personal\n"
    );

    command(&sync_home, &codex_home, &codex_bin)
        .args(["rollback", "--approve"])
        .assert()
        .success();
    assert_eq!(
        fs::read_to_string(agents.join("default.toml")).unwrap(),
        DEFAULT_PROFILE
    );
    assert_eq!(
        fs::read_to_string(agents.join("image.toml")).unwrap(),
        IMAGE_PROFILE
    );
    assert_eq!(
        fs::read_to_string(agents.join("personal.toml")).unwrap(),
        "personal\n"
    );
}

include!("support/cli_sync_tail.rs");
