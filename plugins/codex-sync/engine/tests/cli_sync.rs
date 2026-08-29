#![cfg(unix)]

use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::process::Command;

mod support;

use predicates::prelude::PredicateBooleanExt;
use support::*;

#[test]
fn setup_pull_and_push_use_git_and_fixed_author() {
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
    assert!(fs::read_to_string(codex_home.join("config.toml"))
        .unwrap()
        .contains("model = \"remote\""));
    fs::write(
        codex_home.join("config.toml"),
        "model = \"local\"\nmodel_reasoning_effort = \"xhigh\"\nweb_search = \"live\"\n",
    )
    .unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let check = temp.path().join("check");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), check.to_str().unwrap()],
    );
    let common = fs::read_to_string(check.join("config/common.toml")).unwrap();
    assert!(common.contains("model = \"local\""));
    assert!(common.contains("model_reasoning_effort = \"xhigh\""));
    let log = Command::new("git")
        .current_dir(&check)
        .args(["log", "-1", "--format=%an <%ae>"])
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&log.stdout).trim(),
        "Logic Tan <logictan89@gmail.com>"
    );
}

#[test]
fn pull_bootstraps_provider_credentials_into_installed_plugin_caches() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("provider-bootstrap-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("config/common.toml"),
        r#"model = "remote"
model_provider = "company"

[model_providers.company]
base_url = "https://provider.example/v1"
experimental_bearer_token = "synced-test-token"
requires_openai_auth = true
"#,
    )
    .unwrap();
    fs::write(
        edit.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://example.test/market.git\"\n",
    )
    .unwrap();
    fs::write(
        edit.join("plugins.toml"),
        "plugins = [\"provider-chat-completions@market\", \"provider-imagegen@market\"]\n",
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "add provider plugins"]);
    run_git(&edit, &["push", "origin", "main"]);

    let plugin_cache = codex_home.join("plugins/cache/market");
    let chat_root = plugin_cache.join("provider-chat-completions/0.1.6");
    let image_root = plugin_cache.join("provider-imagegen/0.1.0");
    fs::create_dir_all(&chat_root).unwrap();
    fs::create_dir_all(&image_root).unwrap();
    let markets_json = temp.path().join("markets.json");
    let plugins_json = temp.path().join("plugins.json");
    write_fake_json(
        &markets_json,
        &fake_market_json("market", "https://example.test/market.git", "main", ""),
    );
    write_fake_json(
        &plugins_json,
        &serde_json::json!({
            "installed": [
                {
                    "pluginId": "provider-chat-completions@market",
                    "version": "0.1.6",
                    "source": {"path": chat_root},
                    "installed": true,
                    "enabled": true
                },
                {
                    "pluginId": "provider-imagegen@market",
                    "version": "0.1.0",
                    "source": {"path": image_root},
                    "installed": true,
                    "enabled": true
                }
            ]
        })
        .to_string(),
    );

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
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .args(["pull"])
        .assert()
        .success();

    for root in [&chat_root, &image_root] {
        let directory = root.join(".codex-provider");
        let credential = directory.join("credential.json");
        let raw = fs::read_to_string(&credential).unwrap();
        let payload: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(payload["provider"], "company");
        assert_eq!(
            payload["headers"]["Authorization"],
            "Bearer synced-test-token"
        );
        assert!(payload.get("experimental_bearer_token").is_none());
        assert!(fs::metadata(&directory).unwrap().is_dir());
        assert!(fs::metadata(&credential).unwrap().is_file());
    }

    fs::write(
        edit.join("config/common.toml"),
        r#"model = "remote"
model_provider = "company"

[model_providers.company]
base_url = "https://provider.example/v1"
requires_openai_auth = true
"#,
    )
    .unwrap();
    run_git(&edit, &["add", "config/common.toml"]);
    run_git(&edit, &["commit", "-m", "remove provider credential"]);
    run_git(&edit, &["push", "origin", "main"]);
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .args(["pull"])
        .assert()
        .success()
        .stdout(predicates::str::contains("credential_unavailable"));
    assert!(!chat_root.join(".codex-provider/credential.json").exists());
    assert!(!image_root.join(".codex-provider/credential.json").exists());
}

#[test]
fn push_auto_captures_actor_authorization_header() {
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
    fs::OpenOptions::new()
        .append(true)
        .open(codex_home.join("config.toml"))
        .unwrap()
        .write_all(
            b"\n[model_providers.cpa.http_headers]\n\"x-openai-actor-authorization\" = \"custom\"\n\n[features.code_mode]\ndirect_only_tool_namespaces = [\"image_gen\"]\n",
        )
        .unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success()
        .stdout(predicates::str::contains("Pushed "))
        .stdout(predicates::str::contains(
            "unmanaged local key: model_providers.cpa.http_headers.x-openai-actor-authorization",
        )
        .not())
        .stdout(predicates::str::contains(
            "unmanaged local key: features.code_mode.direct_only_tool_namespaces",
        )
        .not());
    let check = temp.path().join("actor-header-check");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), check.to_str().unwrap()],
    );
    let common = fs::read_to_string(check.join("config/common.toml")).unwrap();
    assert!(common.contains("[model_providers.cpa.http_headers]"));
    assert!(common.contains("x-openai-actor-authorization = \"custom\""));
    assert!(common.contains("direct_only_tool_namespaces = [\"image_gen\"]"));
}

#[test]
fn git_override_is_used_and_invalid_override_is_explicit() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let codex_bin = temp.path().join("codex-cli");
    let wrapper = temp.path().join("git-override");
    let real_git = Command::new("sh")
        .args(["-c", "command -v git"])
        .output()
        .unwrap();
    assert!(real_git.status.success());
    let real_git = String::from_utf8(real_git.stdout)
        .unwrap()
        .trim()
        .to_owned();
    fs::write(
        &wrapper,
        "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$1\" >> \"$CODEX_SYNC_GIT_LOG\"\nexec \"$CODEX_SYNC_REAL_GIT\" \"$@\"\n",
    )
    .unwrap();
    let mut permissions = fs::metadata(&wrapper).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&wrapper, permissions).unwrap();
    let log = temp.path().join("git-override.log");

    command(&codex_home, &sync_home, &codex_bin)
        .env("CODEX_SYNC_GIT_BIN", &wrapper)
        .env("CODEX_SYNC_REAL_GIT", &real_git)
        .env("CODEX_SYNC_GIT_LOG", &log)
        .args([
            "setup",
            "--repository",
            remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .success();
    assert!(fs::read_to_string(&log)
        .unwrap()
        .lines()
        .any(|line| line == "clone"));

    let (missing_temp, missing_remote, missing_codex_home, missing_sync_home) = fixture();
    let missing_codex_bin = missing_temp.path().join("codex-cli");
    command(&missing_codex_home, &missing_sync_home, &missing_codex_bin)
        .env(
            "CODEX_SYNC_GIT_BIN",
            missing_temp.path().join("missing-git"),
        )
        .args([
            "setup",
            "--repository",
            missing_remote.to_str().unwrap(),
            "--device",
            "test",
        ])
        .assert()
        .failure()
        .stderr(predicates::str::contains(
            "CODEX_SYNC_GIT_BIN does not point to a file",
        ));
}

#[test]
fn automations_are_device_local_and_legacy_repository_definitions_are_removed_on_push() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("automation-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::create_dir_all(edit.join("automations/codex-2")).unwrap();
    fs::write(
        edit.join("automations/codex-2/automation.toml"),
        "legacy repository automation\n",
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "add automation"]);
    run_git(&edit, &["push", "origin", "main"]);

    let local_definition = codex_home.join("automations/local-job/automation.toml");
    let local_memory = codex_home.join("automations/local-job/memory.md");
    fs::create_dir_all(local_definition.parent().unwrap()).unwrap();
    fs::write(&local_definition, "local automation\n").unwrap();
    fs::write(&local_memory, "local runtime memory\n").unwrap();

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
        .args(["pull", "--dry-run"])
        .assert()
        .success()
        .stdout(predicates::str::contains("automation").not());
    assert!(!codex_home
        .join("automations/codex-2/automation.toml")
        .exists());
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    assert_eq!(
        fs::read_to_string(&local_definition).unwrap(),
        "local automation\n"
    );
    assert_eq!(
        fs::read_to_string(&local_memory).unwrap(),
        "local runtime memory\n"
    );
    assert!(!codex_home
        .join("automations/codex-2/automation.toml")
        .exists());

    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let check = temp.path().join("automation-check");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), check.to_str().unwrap()],
    );
    assert!(!check.join("automations").exists());
    assert_eq!(
        fs::read_to_string(&local_definition).unwrap(),
        "local automation\n"
    );
    assert_eq!(
        fs::read_to_string(&local_memory).unwrap(),
        "local runtime memory\n"
    );
}

#[test]
fn device_overlay_capture_keeps_shadowed_common_baseline() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("overlay-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("config/common.toml"),
        "model = \"A\"\nmodel_reasoning_effort = \"high\"\n",
    )
    .unwrap();
    fs::write(
        edit.join("devices/test.toml"),
        "model = \"B\"\nweb_search = \"live\"\n",
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "overlay"]);
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
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let check = temp.path().join("overlay-check");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), check.to_str().unwrap()],
    );
    assert!(fs::read_to_string(check.join("config/common.toml"))
        .unwrap()
        .contains("model = \"A\""));
    assert!(fs::read_to_string(check.join("devices/test.toml"))
        .unwrap()
        .contains("model = \"B\""));
}

#[test]
fn pull_removes_managed_remote_deletion_and_preserves_unmanaged_local_key() {
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
    fs::OpenOptions::new()
        .append(true)
        .open(codex_home.join("config.toml"))
        .unwrap()
        .write_all(b"custom_local = \"keep\"\n")
        .unwrap();
    let edit = temp.path().join("delete-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("config/common.toml"),
        "model_reasoning_effort = \"high\"\n",
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "delete model"]);
    run_git(&edit, &["push", "origin", "main"]);
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    let config = fs::read_to_string(codex_home.join("config.toml")).unwrap();
    assert!(!config.contains("model = \"remote\""));
    assert!(config.contains("custom_local = \"keep\""));
}

#[test]
fn dry_run_does_not_create_a_remote_commit() {
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
    let before = Command::new("git")
        .args([
            "--git-dir",
            remote.to_str().unwrap(),
            "rev-parse",
            "refs/heads/main",
        ])
        .output()
        .unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push", "--dry-run"])
        .assert()
        .success();
    let after = Command::new("git")
        .args([
            "--git-dir",
            remote.to_str().unwrap(),
            "rev-parse",
            "refs/heads/main",
        ])
        .output()
        .unwrap();
    assert_eq!(before.stdout, after.stdout);
}

#[test]
fn push_rejects_remote_race_and_retries_from_latest_baseline() {
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

    let race_clone = temp.path().join("race-clone");
    run_git(
        temp.path(),
        &[
            "clone",
            remote.to_str().unwrap(),
            race_clone.to_str().unwrap(),
        ],
    );
    run_git(&race_clone, &["config", "user.name", "Concurrent"]);
    run_git(
        &race_clone,
        &["config", "user.email", "concurrent@example.test"],
    );
    fs::write(race_clone.join("concurrent.txt"), "concurrent\n").unwrap();
    run_git(&race_clone, &["add", "concurrent.txt"]);
    run_git(&race_clone, &["commit", "-m", "concurrent update"]);

    fs::write(
        codex_home.join("config.toml"),
        "model = \"before-race\"\nmodel_reasoning_effort = \"high\"\nweb_search = \"live\"\n",
    )
    .unwrap();
    let wrapper_dir = temp.path().join("git-wrapper");
    fs::create_dir_all(&wrapper_dir).unwrap();
    let wrapper = wrapper_dir.join("git");
    let real_git = Command::new("sh")
        .args(["-c", "command -v git"])
        .output()
        .unwrap();
    assert!(real_git.status.success());
    let real_git = String::from_utf8(real_git.stdout)
        .unwrap()
        .trim()
        .to_owned();
    fs::write(
        &wrapper,
        "#!/bin/sh\nset -eu\nif [ \"$1\" = \"status\" ] && [ \"$2\" = \"--porcelain\" ] && [ ! -e \"$RACE_DONE\" ]; then\n  touch \"$RACE_DONE\"\n  \"$CODEX_SYNC_REAL_GIT\" -C \"$RACE_CLONE\" push origin main\nfi\nexec \"$CODEX_SYNC_REAL_GIT\" \"$@\"\n",
    )
    .unwrap();
    let mut permissions = fs::metadata(&wrapper).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&wrapper, permissions).unwrap();
    let path = std::env::var_os("PATH").unwrap_or_default();
    let path = format!("{}:{}", wrapper_dir.display(), path.to_string_lossy());
    let race_done = temp.path().join("race-done");

    command(&codex_home, &sync_home, &codex_bin)
        .env("PATH", &path)
        .env("CODEX_SYNC_REAL_GIT", &real_git)
        .env("RACE_CLONE", &race_clone)
        .env("RACE_DONE", &race_done)
        .args(["push"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("remote branch advanced"));
    assert!(race_done.exists());
    assert!(race_clone.join("concurrent.txt").exists());

    let remote_check = temp.path().join("race-check");
    run_git(
        temp.path(),
        &[
            "clone",
            remote.to_str().unwrap(),
            remote_check.to_str().unwrap(),
        ],
    );
    assert_eq!(
        fs::read_to_string(remote_check.join("concurrent.txt")).unwrap(),
        "concurrent\n"
    );

    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    fs::write(
        codex_home.join("config.toml"),
        "model = \"after-race\"\nmodel_reasoning_effort = \"high\"\nweb_search = \"live\"\n",
    )
    .unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let final_check = temp.path().join("race-final");
    run_git(
        temp.path(),
        &[
            "clone",
            remote.to_str().unwrap(),
            final_check.to_str().unwrap(),
        ],
    );
    assert!(final_check.join("concurrent.txt").exists());
    assert!(fs::read_to_string(final_check.join("config/common.toml"))
        .unwrap()
        .contains("model = \"after-race\""));
}

#[test]
fn market_source_ref_and_sparse_replacement_detaches_plugins_first() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("market-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://old.test/market.git\"\ngit_ref = \"main\"\nsparse = [\"old/plugins\"]\n",
    )
    .unwrap();
    fs::write(edit.join("plugins.toml"), "plugins = [\"old@market\"]\n").unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "old market"]);
    run_git(&edit, &["push", "origin", "main"]);

    let codex_bin = temp.path().join("codex-cli");
    let markets_json = temp.path().join("markets.json");
    let plugins_json = temp.path().join("plugins.json");
    write_fake_json(
        &markets_json,
        &fake_market_json(
            "market",
            "https://old.test/market.git",
            "main",
            "old/plugins",
        ),
    );
    write_fake_json(&plugins_json, &fake_plugin_json(&["old@market"]));
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
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .args(["pull"])
        .assert()
        .success();

    fs::write(
        edit.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://new.test/market.git\"\ngit_ref = \"release\"\nsparse = [\"new/plugins\"]\n",
    )
    .unwrap();
    fs::write(
        edit.join("plugins.toml"),
        "plugins = [\"desired@market\"]\n",
    )
    .unwrap();
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "replace market source"]);
    run_git(&edit, &["push", "origin", "main"]);

    let log = temp.path().join("market-actions.log");
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .env("FAKE_CODEX_LOG", &log)
        .args(["pull"])
        .assert()
        .success();
    let actions = fs::read_to_string(log).unwrap();
    let lines = actions.lines().collect::<Vec<_>>();
    assert_eq!(
        lines,
        vec![
            "plugin remove old@market",
            "plugin marketplace remove market",
            "plugin marketplace add https://new.test/market.git --ref release --sparse new/plugins",
            "plugin marketplace upgrade market",
            "plugin add desired@market",
        ]
    );
}

#[test]
fn pull_removes_managed_market_but_protects_unmanaged_and_openai_items() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("protection-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"managed\"\nurl = \"https://example.test/managed.git\"\n",
    )
    .unwrap();
    fs::write(edit.join("plugins.toml"), "plugins = [\"old@managed\"]\n").unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "managed market"]);
    run_git(&edit, &["push", "origin", "main"]);

    let codex_bin = temp.path().join("codex-cli");
    let markets_json = temp.path().join("protected-markets.json");
    let plugins_json = temp.path().join("protected-plugins.json");
    write_fake_json(
        &markets_json,
        r#"{"marketplaces":[
          {"name":"managed","marketplaceSource":{"sourceType":"git","source":"https://example.test/managed.git","ref":"main"}},
          {"name":"personal","marketplaceSource":{"sourceType":"git","source":"https://example.test/personal.git","ref":"main"}},
          {"name":"openai-bundled","marketplaceSource":{"sourceType":"git","source":"https://example.test/openai.git","ref":"main"}}
        ]}"#,
    );
    write_fake_json(
        &plugins_json,
        &fake_plugin_json(&["old@managed", "local@personal", "browser@openai-bundled"]),
    );
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
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .args(["pull"])
        .assert()
        .success();

    fs::write(edit.join("marketplaces.toml"), "marketplaces = []\n").unwrap();
    fs::write(edit.join("plugins.toml"), "plugins = []\n").unwrap();
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "remove managed market"]);
    run_git(&edit, &["push", "origin", "main"]);
    let log = temp.path().join("protection-actions.log");
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .env("FAKE_CODEX_LOG", &log)
        .args(["pull"])
        .assert()
        .success();
    let actions = fs::read_to_string(log).unwrap();
    assert!(actions.contains("plugin remove old@managed"));
    assert!(actions.contains("plugin marketplace remove managed"));
    assert!(!actions.contains("local@personal"));
    assert!(!actions.contains("personal\n"));
    assert!(!actions.contains("browser@openai-bundled"));
    assert!(!actions.contains("openai-bundled\n"));
}

#[test]
fn failed_plugin_convergence_retries_without_losing_core_state() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("plugin-failure-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(
        edit.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://example.test/market.git\"\n",
    )
    .unwrap();
    fs::write(edit.join("plugins.toml"), "plugins = [\"fail@market\"]\n").unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "plugin failure"]);
    run_git(&edit, &["push", "origin", "main"]);

    let codex_bin = temp.path().join("codex-cli");
    let markets_json = temp.path().join("failure-markets.json");
    let plugins_json = temp.path().join("failure-plugins.json");
    write_fake_json(
        &markets_json,
        &fake_market_json("market", "https://example.test/market.git", "main", ""),
    );
    write_fake_json(&plugins_json, &fake_plugin_json(&[]));
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
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .env("FAKE_CODEX_FAIL_ID", "fail@market")
        .args(["pull"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("convergence failed"));
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("converged = false"));
    assert!(codex_home.join("config.toml").exists());

    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .env("FAKE_CODEX_LOG", temp.path().join("retry-actions.log"))
        .args(["pull"])
        .assert()
        .success();
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("converged = true"));
}

#[test]
fn v2_repository_is_migrated_on_first_push() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let legacy = temp.path().join("legacy");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), legacy.to_str().unwrap()],
    );
    fs::create_dir_all(legacy.join("old")).unwrap();
    fs::write(
        legacy.join("codex-sync.toml"),
        "schema_version = 2\nagents = \"AGENTS.md\"\nagent_profiles = \"agents\"\ncommon_config = \"old/common.toml\"\ndevices = \"devices\"\nmarketplaces = \"marketplaces.toml\"\nplugins = \"plugins.toml\"\nproviders = \"old/providers.toml\"\n",
    )
    .unwrap();
    fs::write(legacy.join("README.md"), "Keep this documentation\n").unwrap();
    fs::write(legacy.join("old/common.toml"), "model = \"v2\"\n").unwrap();
    fs::write(
        legacy.join("old/providers.toml"),
        "[providers.company]\nbase_url = \"https://example.test/v1\"\n",
    )
    .unwrap();
    fs::write(
        legacy.join("plugins.toml"),
        "[[plugins]]\nid = \"disabled@market\"\nenabled = false\n\n[[plugins]]\nid = \"enabled@market\"\nenabled = true\nauto_provision = true\n",
    )
    .unwrap();
    fs::write(
        legacy.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://example.test/market.git\"\n",
    )
    .unwrap();
    run_git(&legacy, &["config", "user.name", "Seed"]);
    run_git(&legacy, &["config", "user.email", "seed@example.test"]);
    run_git(&legacy, &["add", "."]);
    run_git(&legacy, &["commit", "-m", "legacy"]);
    run_git(&legacy, &["push", "origin", "main"]);
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
    fs::create_dir_all(sync_home.join("backups")).unwrap();
    fs::create_dir_all(sync_home.join("provision-artifacts")).unwrap();
    fs::create_dir_all(sync_home.join("provision-operations")).unwrap();
    fs::create_dir_all(sync_home.join("marketplaces")).unwrap();
    fs::write(sync_home.join("pending-plan.json"), "pending\n").unwrap();
    fs::create_dir_all(sync_home.join("setup-backups")).unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    assert!(!codex_home.join("config.toml").exists());
    assert!(sync_home.join("backups").exists());
    fs::write(
        codex_home.join("config.toml"),
        "model = \"v2\"\n[model_providers.company]\nbase_url = \"https://example.test/v1\"\n",
    )
    .unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let check = temp.path().join("migrated");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), check.to_str().unwrap()],
    );
    assert_eq!(
        fs::read_to_string(check.join("codex-sync.toml"))
            .unwrap()
            .trim(),
        "schema_version = 3"
    );
    assert_eq!(
        fs::read_to_string(check.join("README.md")).unwrap(),
        "Keep this documentation\n"
    );
    let common = fs::read_to_string(check.join("config/common.toml")).unwrap();
    assert!(common.contains("model_providers"));
    let plugins = fs::read_to_string(check.join("plugins.toml")).unwrap();
    assert!(!plugins.contains("disabled@market"));

    // Migration cleanup is gated on a successful v3 pull. A plugin failure
    // after the migration push must retain every legacy directory for retry.
    assert!(sync_home.join("backups").exists());
    assert!(sync_home.join("provision-artifacts").exists());
    let post_migration = temp.path().join("post-migration");
    run_git(
        temp.path(),
        &[
            "clone",
            remote.to_str().unwrap(),
            post_migration.to_str().unwrap(),
        ],
    );
    fs::write(
        post_migration.join("marketplaces.toml"),
        "[[marketplaces]]\nsource = \"git\"\nname = \"market\"\nurl = \"https://example.test/market.git\"\n",
    )
    .unwrap();
    fs::write(
        post_migration.join("plugins.toml"),
        "plugins = [\"fail@market\"]\n",
    )
    .unwrap();
    run_git(&post_migration, &["config", "user.name", "Seed"]);
    run_git(
        &post_migration,
        &["config", "user.email", "seed@example.test"],
    );
    run_git(&post_migration, &["add", "."]);
    run_git(&post_migration, &["commit", "-m", "post migration plugin"]);
    run_git(&post_migration, &["push", "origin", "main"]);

    let markets_json = temp.path().join("migration-markets.json");
    let plugins_json = temp.path().join("migration-plugins.json");
    write_fake_json(
        &markets_json,
        &fake_market_json("market", "https://example.test/market.git", "main", ""),
    );
    write_fake_json(&plugins_json, &fake_plugin_json(&[]));
    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .env("FAKE_CODEX_FAIL_ID", "fail@market")
        .args(["pull"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("convergence failed"));
    assert!(fs::read_to_string(sync_home.join("state.toml"))
        .unwrap()
        .contains("converged = false"));
    assert!(sync_home.join("backups").exists());
    assert!(sync_home.join("provision-artifacts").exists());
    assert!(sync_home.join("provision-operations").exists());
    assert!(sync_home.join("marketplaces").exists());
    assert!(sync_home.join("pending-plan.json").exists());
    assert!(sync_home.join("setup-backups").exists());

    command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_MARKETS_JSON", &markets_json)
        .env("FAKE_CODEX_PLUGINS_JSON", &plugins_json)
        .args(["pull"])
        .assert()
        .success();
    for path in [
        "backups",
        "provision-artifacts",
        "provision-operations",
        "marketplaces",
        "pending-plan.json",
        "setup-backups",
    ] {
        assert!(
            !sync_home.join(path).exists(),
            "legacy path still exists: {path}"
        );
    }
    assert!(sync_home.join("backup/previous").exists());
    let state = fs::read_to_string(sync_home.join("state.toml")).unwrap();
    assert!(state.contains("migration_cleanup_pending = false"));
    assert!(state.contains("converged = true"));
}

#[test]
fn push_rejects_probable_secret_but_allows_provider_bearer_exception() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("secret-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::create_dir_all(edit.join("agents")).unwrap();
    fs::write(edit.join("config/common.toml"), "api_key = \"nope\"\n").unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "secret"]);
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
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("probable secret"));
}

#[test]
fn malformed_v3_arrays_fail_instead_of_becoming_empty() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("schema-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::write(edit.join("marketplaces.toml"), "market = []\n").unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "malformed schema"]);
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
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .failure()
        .stderr(predicates::str::contains("top-level marketplaces array"));
}

#[test]
fn profile_only_pull_dry_run_reports_without_mutating_codex() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("profile-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::create_dir_all(edit.join("agents")).unwrap();
    fs::write(
        edit.join("agents/new.toml"),
        "name = \"new\"\ndescription = \"New\"\ndeveloper_instructions = \"Do work\"\n",
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "profile"]);
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
    let log = temp.path().join("codex-actions.log");
    let output = command(&codex_home, &sync_home, &codex_bin)
        .env("FAKE_CODEX_LOG", &log)
        .args(["pull", "--dry-run"])
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).contains("add agent profile new.toml"));
    assert!(!log.exists());
    assert!(!codex_home.join("agents/new.toml").exists());
}
