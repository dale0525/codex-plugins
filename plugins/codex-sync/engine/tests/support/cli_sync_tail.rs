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
    let config = fs::read_to_string(codex_home.join("config.toml")).unwrap();
    assert!(config.contains("env_key = \"COMPANY_OPENAI_API_KEY\""));
    assert!(config.contains("experimental_bearer_token = \"test-provider-bearer-token\""));
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
fn removed_plugin_spec_uninstalls_managed_plugin() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    let codex_bin = temporary.path().join("codex-stateful");
    let plugin_state = temporary.path().join("plugin-state");
    let marketplace_state = temporary.path().join("marketplace-state");
    fs::write(&plugin_state, "subagent-dispatch@market|true").unwrap();
    fs::write(&marketplace_state, "").unwrap();
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
    let (api_url, server) = serve_github(
        "abc123",
        repository_zip_with_managed_marketplace_and_no_plugins(),
    );
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(output.status.success());
    server.join().unwrap();
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("plugin subagent-dispatch@market"));
    assert!(stdout.contains("remove plugin no longer declared"));
    let plan_id = stdout
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap();

    command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
        .args(["apply", plan_id, "--approve-high-risk"])
        .assert()
        .success()
        .stdout(predicates::str::contains(
            "removed plugin subagent-dispatch@market",
        ));

    assert_eq!(fs::read_to_string(plugin_state).unwrap(), "");
}

#[test]
fn existing_git_marketplace_is_upgraded_without_destructive_reregistration() {
    let temporary = tempfile::tempdir().unwrap();
    let sync_home = temporary.path().join("sync");
    let codex_home = temporary.path().join("codex");
    fs::create_dir_all(&codex_home).unwrap();
    fs::write(codex_home.join("config.toml"), "model = \"old\"\n").unwrap();
    fs::write(codex_home.join("AGENTS.md"), "# Old\n").unwrap();

    let marketplace = temporary.path().join("marketplace");
    let launch_directory = temporary.path().join("installed-plugin");
    fs::create_dir_all(&marketplace).unwrap();
    fs::create_dir_all(&launch_directory).unwrap();
    fs::write(marketplace.join("sentinel.txt"), "original\n").unwrap();
    let codex_bin = temporary.path().join("codex-stateful");
    let plugin_state = temporary.path().join("plugin-state");
    let marketplace_state = temporary.path().join("marketplace-state");
    fs::write(&plugin_state, "").unwrap();
    fs::write(&marketplace_state, marketplace.to_string_lossy().as_bytes()).unwrap();
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
    let (api_url, server) = serve_github(
        "abc123",
        repository_zip_with_managed_marketplace_and_no_plugins(),
    );
    let output = command(&sync_home, &codex_home, &codex_bin)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
        .env("CODEX_SYNC_GITHUB_API_URL", api_url)
        .arg("sync")
        .output()
        .unwrap();
    assert!(output.status.success());
    server.join().unwrap();
    let plan_id = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .find_map(|line| line.strip_prefix("Plan "))
        .and_then(|line| line.split_whitespace().next())
        .unwrap()
        .to_owned();

    command(&sync_home, &codex_home, &codex_bin)
        .current_dir(&launch_directory)
        .env("FAKE_CODEX_STATE", &plugin_state)
        .env("FAKE_MARKETPLACE_STATE", &marketplace_state)
        .env("FAKE_MARKETPLACE_SOURCE", "https://example.com/market.git")
        .env("FAKE_DAMAGE_MARKETPLACE_ON_REMOVE", "1")
        .env("FAKE_REMOVE_CWD_ON_UPGRADE", &launch_directory)
        .args(["apply", &plan_id, "--approve-high-risk"])
        .assert()
        .success();

    assert!(!launch_directory.exists());
    assert_eq!(
        fs::read_to_string(marketplace.join("sentinel.txt")).unwrap(),
        "original\n"
    );
    assert_eq!(
        fs::read_to_string(&marketplace_state).unwrap(),
        marketplace.to_string_lossy()
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
