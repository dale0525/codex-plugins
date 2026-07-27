use super::*;

#[test]
fn device_ids_are_portable() {
    assert!(validate_device_id("mac-studio_1").is_ok());
    assert!(validate_device_id("bad/device").is_err());
    assert!(validate_device_id("").is_err());
}

#[test]
fn publish_secret_scan_rejects_private_keys() {
    let directory = tempfile::tempdir().unwrap();
    fs::write(directory.path().join("credential.pem"), "not even a key").unwrap();
    assert!(scan_repository_for_obvious_secrets(directory.path()).is_err());
}

#[test]
fn restore_removes_files_that_were_absent_before_apply() {
    let directory = tempfile::tempdir().unwrap();
    let codex_home = directory.path().join("codex");
    let data_home = directory.path().join("sync");
    let backup = data_home.join("backups/example");
    fs::create_dir_all(&codex_home).unwrap();
    fs::create_dir_all(&backup).unwrap();
    fs::write(codex_home.join("config.toml"), "model = \"new\"\n").unwrap();
    fs::write(codex_home.join("AGENTS.md"), "new\n").unwrap();
    fs::write(backup.join("config.toml.absent"), "").unwrap();
    fs::write(backup.join("AGENTS.md.absent"), "").unwrap();
    let paths = crate::model::Paths {
        state_file: data_home.join("state.toml"),
        lock_file: data_home.join("sync.lock"),
        repository_dir: data_home.join("repository"),
        marketplaces_dir: data_home.join("marketplaces"),
        backups_dir: data_home.join("backups"),
        pending_plan: data_home.join("pending-plan.json"),
        data_home,
        codex_home: codex_home.clone(),
    };
    restore_backup(&paths, &backup).unwrap();
    assert!(!codex_home.join("config.toml").exists());
    assert!(!codex_home.join("AGENTS.md").exists());
}
