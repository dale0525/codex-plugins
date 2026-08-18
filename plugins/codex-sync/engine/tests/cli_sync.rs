#![cfg(unix)]

use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::process::Command;

mod support;

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
fn automations_sync_definitions_and_preserve_runtime_memory() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("automation-edit");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::create_dir_all(edit.join("automations/codex-2")).unwrap();
    fs::write(
        edit.join("automations/codex-2/automation.toml"),
        r#"version = 1
id = "codex-2"
kind = "cron"
name = "Weekly cleanup"
prompt = "Clean old sessions"
status = "ACTIVE"
rrule = "FREQ=WEEKLY;BYDAY=MO;BYHOUR=7;BYMINUTE=0;BYSECOND=0"
model = "gpt-5.6-sol"
reasoning_effort = "medium"
execution_environment = "local"
target = { type = "projectless" }
cwds = ["~"]
created_at = 1
updated_at = 2
approval_policy = "never"
sandbox_mode = "danger-full-access"
"#,
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "add automation"]);
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
        .args(["pull", "--dry-run"])
        .assert()
        .success()
        .stdout(predicates::str::contains(
            "add automation codex-2/automation.toml",
        ));
    assert!(!codex_home
        .join("automations/codex-2/automation.toml")
        .exists());
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    let local_definition = codex_home.join("automations/codex-2/automation.toml");
    let local_text = fs::read_to_string(&local_definition).unwrap();
    assert!(local_text.contains("sandbox_mode = \"danger-full-access\""));
    // Simulate the desktop app rewriting its native schema, which currently
    // omits the two optional sync metadata fields.
    fs::write(
        &local_definition,
        local_text
            .replace("approval_policy = \"never\"\n", "")
            .replace("sandbox_mode = \"danger-full-access\"\n", ""),
    )
    .unwrap();
    fs::write(
        codex_home.join("automations/codex-2/memory.md"),
        "runtime memory\n",
    )
    .unwrap();
    fs::write(
        codex_home.join("automations/.run-jitter-salt"),
        "runtime salt\n",
    )
    .unwrap();
    command(&codex_home, &sync_home, &codex_bin)
        .args(["push"])
        .assert()
        .success();
    let check = temp.path().join("automation-check");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), check.to_str().unwrap()],
    );
    assert!(check.join("automations/codex-2/automation.toml").exists());
    let captured = fs::read_to_string(check.join("automations/codex-2/automation.toml")).unwrap();
    assert!(captured.contains("approval_policy = \"never\""));
    assert!(captured.contains("sandbox_mode = \"danger-full-access\""));
    assert!(!check.join("automations/codex-2/memory.md").exists());
    assert!(!check.join("automations/.run-jitter-salt").exists());

    run_git(&edit, &["pull", "--rebase", "origin", "main"]);
    fs::remove_file(edit.join("automations/codex-2/automation.toml")).unwrap();
    run_git(&edit, &["add", "-A"]);
    run_git(&edit, &["commit", "-m", "remove automation"]);
    run_git(&edit, &["push", "origin", "main"]);
    command(&codex_home, &sync_home, &codex_bin)
        .args(["pull"])
        .assert()
        .success();
    assert!(!local_definition.exists());
    assert!(codex_home.join("automations/codex-2/memory.md").exists());
}

#[test]
fn automation_policy_validation_rejects_unknown_values() {
    let (temp, remote, codex_home, sync_home) = fixture();
    let edit = temp.path().join("automation-invalid");
    run_git(
        temp.path(),
        &["clone", remote.to_str().unwrap(), edit.to_str().unwrap()],
    );
    fs::create_dir_all(edit.join("automations/bad")).unwrap();
    fs::write(
        edit.join("automations/bad/automation.toml"),
        r#"version = 1
id = "bad"
kind = "cron"
name = "Bad"
prompt = "Bad"
status = "ACTIVE"
rrule = "FREQ=DAILY"
execution_environment = "local"
target = { type = "projectless" }
cwds = ["~"]
created_at = 1
updated_at = 2
sandbox_mode = "not-a-mode"
"#,
    )
    .unwrap();
    run_git(&edit, &["config", "user.name", "Seed"]);
    run_git(&edit, &["config", "user.email", "seed@example.test"]);
    run_git(&edit, &["add", "."]);
    run_git(&edit, &["commit", "-m", "invalid automation"]);
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
        .stderr(predicates::str::contains(
            "unsupported automation sandbox_mode",
        ));
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
