use super::*;
#[cfg(unix)]
use crate::provision::OperationAction;
use std::collections::BTreeMap;

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

#[test]
fn recovery_required_gate_rejects_apply() {
    let state = LocalState {
        schema_version: LOCAL_STATE_SCHEMA_VERSION,
        repository: RepositoryRef::parse("owner/repo", "main".to_owned()).unwrap(),
        device_id: "test".to_owned(),
        github_client_id: None,
        last_fetched_commit: None,
        fetched_repository_sha256: None,
        last_applied_commit: None,
        managed_paths: Vec::new(),
        managed_agent_profiles: Vec::new(),
        latest_backup: None,
        provision_receipts: BTreeMap::new(),
        operation_log: None,
        recovery_required: true,
    };
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::model::Paths {
        data_home: directory.path().to_owned(),
        state_file: directory.path().join("state.toml"),
        lock_file: directory.path().join("lock"),
        repository_dir: directory.path().join("repository"),
        marketplaces_dir: directory.path().join("marketplaces"),
        backups_dir: directory.path().join("backups"),
        pending_plan: directory.path().join("pending-plan.json"),
        codex_home: directory.path().join("codex"),
    };
    let error = ensure_recovery_clear(&paths, &state).unwrap_err();
    assert!(error.to_string().contains("recovery-required"));
}

#[test]
fn rollback_failure_persists_recovery_safety_checkpoint() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("rollback.json");
    let log = OperationLog {
        schema_version: 1,
        operation_id: "rollback-test".to_owned(),
        kind: "rollback".to_owned(),
        phase: "recovery_required".to_owned(),
        actions: vec!["tool@market:setup".to_owned()],
        action_records: Vec::new(),
        backup: Some("backup-test".to_owned()),
        recovery_required: true,
        before_backup: None,
        target_state: None,
        before_state_digest: None,
        target_state_digest: None,
        supersedes: None,
        compensation_steps: Vec::new(),
    };
    write_operation_log(&path, &log).unwrap();
    let persisted = read_operation_log(&path).unwrap();
    assert_eq!(persisted.phase, "recovery_required");
    assert!(operation_needs_recovery(&persisted));
    assert_eq!(persisted.backup.as_deref(), Some("backup-test"));
}

#[test]
fn orphan_started_operation_blocks_new_apply() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::model::Paths {
        data_home: directory.path().to_owned(),
        state_file: directory.path().join("state.toml"),
        lock_file: directory.path().join("lock"),
        repository_dir: directory.path().join("repository"),
        marketplaces_dir: directory.path().join("marketplaces"),
        backups_dir: directory.path().join("backups"),
        pending_plan: directory.path().join("pending-plan.json"),
        codex_home: directory.path().join("codex"),
    };
    let log_path = operation_log_path(&paths.data_home, "orphan");
    write_operation_log(
        &log_path,
        &OperationLog {
            schema_version: 1,
            operation_id: "orphan".to_owned(),
            kind: "apply".to_owned(),
            phase: "runtime_started".to_owned(),
            actions: vec!["tool@market:setup".to_owned()],
            action_records: Vec::new(),
            backup: Some("backup".to_owned()),
            recovery_required: false,
            before_backup: None,
            target_state: None,
            before_state_digest: None,
            target_state_digest: None,
            supersedes: None,
            compensation_steps: Vec::new(),
        },
    )
    .unwrap();
    let state = LocalState {
        schema_version: LOCAL_STATE_SCHEMA_VERSION,
        repository: RepositoryRef::parse("owner/repo", "main".to_owned()).unwrap(),
        device_id: "test".to_owned(),
        github_client_id: None,
        last_fetched_commit: None,
        fetched_repository_sha256: None,
        last_applied_commit: None,
        managed_paths: Vec::new(),
        managed_agent_profiles: Vec::new(),
        latest_backup: None,
        provision_receipts: BTreeMap::new(),
        operation_log: None,
        recovery_required: false,
    };
    let error = ensure_recovery_clear(&paths, &state).unwrap_err();
    assert!(error
        .to_string()
        .contains("unfinished operation checkpoint"));
}

#[test]
fn multiple_unfinished_logs_are_reported_deterministically() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::model::Paths {
        data_home: directory.path().to_owned(),
        state_file: directory.path().join("state.toml"),
        lock_file: directory.path().join("lock"),
        repository_dir: directory.path().join("repository"),
        marketplaces_dir: directory.path().join("marketplaces"),
        backups_dir: directory.path().join("backups"),
        pending_plan: directory.path().join("pending-plan.json"),
        codex_home: directory.path().join("codex"),
    };
    for id in ["b", "a"] {
        write_operation_log(
            &operation_log_path(&paths.data_home, id),
            &OperationLog {
                schema_version: 1,
                operation_id: id.to_owned(),
                kind: "apply".to_owned(),
                phase: "runtime_started".to_owned(),
                actions: Vec::new(),
                action_records: Vec::new(),
                backup: Some("backup".to_owned()),
                recovery_required: false,
                before_backup: None,
                target_state: None,
                before_state_digest: None,
                target_state_digest: None,
                supersedes: None,
                compensation_steps: Vec::new(),
            },
        )
        .unwrap();
    }
    let state = LocalState {
        schema_version: LOCAL_STATE_SCHEMA_VERSION,
        repository: RepositoryRef::parse("owner/repo", "main".to_owned()).unwrap(),
        device_id: "test".to_owned(),
        github_client_id: None,
        last_fetched_commit: None,
        fetched_repository_sha256: None,
        last_applied_commit: None,
        managed_paths: Vec::new(),
        managed_agent_profiles: Vec::new(),
        latest_backup: None,
        provision_receipts: BTreeMap::new(),
        operation_log: None,
        recovery_required: false,
    };
    let error = ensure_recovery_clear(&paths, &state).unwrap_err();
    let text = error.to_string();
    assert!(text.contains("multiple unfinished operation checkpoints"));
    assert!(text.find("a.json").unwrap() < text.find("b.json").unwrap());
}

#[cfg(unix)]
#[test]
fn completed_wal_action_is_consumed_after_stale_state_crash_window() {
    let directory = tempfile::tempdir().unwrap();
    let sync_home = directory.path().join("sync");
    let codex_home = directory.path().join("codex");
    let backup = sync_home.join("backups/before");
    fs::create_dir_all(codex_home.join("agents")).unwrap();
    fs::create_dir_all(&backup).unwrap();
    fs::write(codex_home.join("config.toml"), "new\n").unwrap();
    fs::write(codex_home.join("AGENTS.md"), "new\n").unwrap();
    fs::write(backup.join("config.toml.absent"), "").unwrap();
    fs::write(backup.join("AGENTS.md.absent"), "").unwrap();

    let root = directory.path().join("artifact");
    fs::create_dir_all(root.join(".codex-sync")).unwrap();
    let spec = br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#;
    fs::write(root.join(".codex-sync/provision.json"), spec).unwrap();
    fs::write(
        root.join("provision.sh"),
        "#!/bin/sh\nprintf uninstall > \"$RECOVERY_SENTINEL\"\n",
    )
    .unwrap();
    let artifact = crate::artifact::materialize(&root, directory.path()).unwrap();
    let script = artifact.root.join("provision.sh");
    let receipt = crate::model::ProvisionReceipt {
        schema_version: 2,
        plugin_id: "crash@market".to_owned(),
        artifact_digest: artifact.digest.clone(),
        artifact_root: artifact.root.to_string_lossy().into_owned(),
        spec_sha256: hex::encode(Sha256::digest(spec)),
        script_sha256: hex::encode(Sha256::digest(fs::read(&script).unwrap())),
        dependencies_sha256: tree_sha256(&artifact.root).unwrap(),
        setup_args: vec!["setup".to_owned()],
        uninstall_args: vec!["uninstall".to_owned()],
        windows_shell: "Pwsh".to_owned(),
        plugin_root: artifact.root.to_string_lossy().into_owned(),
        script: script.to_string_lossy().into_owned(),
        provisioned_at: "test".to_owned(),
    };
    let target_state = LocalState {
        schema_version: LOCAL_STATE_SCHEMA_VERSION,
        repository: RepositoryRef::parse("owner/repo", "main".to_owned()).unwrap(),
        device_id: "test".to_owned(),
        github_client_id: None,
        last_fetched_commit: None,
        fetched_repository_sha256: None,
        last_applied_commit: None,
        managed_paths: Vec::new(),
        managed_agent_profiles: Vec::new(),
        latest_backup: None,
        provision_receipts: BTreeMap::new(),
        operation_log: None,
        recovery_required: false,
    };
    fs::create_dir_all(backup.parent().unwrap()).unwrap();
    fs::write(
        backup.join("state.toml"),
        toml::to_string(&target_state).unwrap(),
    )
    .unwrap();
    let operation_path = operation_log_path(&sync_home, "crash");
    let operation = OperationLog {
        schema_version: 3,
        operation_id: "crash".to_owned(),
        kind: "apply".to_owned(),
        phase: "commit-prepared".to_owned(),
        actions: Vec::new(),
        action_records: vec![OperationAction {
            action_id: "crash-a1".to_owned(),
            plugin_id: receipt.plugin_id.clone(),
            receipt: receipt.clone(),
            previous: None,
            uninstall: false,
            kind: "provision".to_owned(),
            status: ActionStatus::Completed,
            message: Some("completed".to_owned()),
            operation_kind: "setup".to_owned(),
            phase: "completed".to_owned(),
            before_receipt: None,
            after_receipt: Some(receipt.clone()),
        }],
        backup: Some("before".to_owned()),
        recovery_required: false,
        before_backup: Some(backup.to_string_lossy().into_owned()),
        target_state: None,
        before_state_digest: None,
        target_state_digest: None,
        supersedes: None,
        compensation_steps: Vec::new(),
    };
    create_operation_log(&operation_path, &operation).unwrap();
    let paths = crate::model::Paths {
        data_home: sync_home.clone(),
        state_file: sync_home.join("state.toml"),
        lock_file: sync_home.join("lock"),
        repository_dir: sync_home.join("repository"),
        marketplaces_dir: sync_home.join("marketplaces"),
        backups_dir: sync_home.join("backups"),
        pending_plan: sync_home.join("pending-plan.json"),
        codex_home,
    };
    let mut current_state = target_state.clone();
    current_state
        .provision_receipts
        .insert(receipt.plugin_id.clone(), receipt);
    current_state.operation_log = Some(operation_path.to_string_lossy().into_owned());
    current_state.recovery_required = true;
    save_state(&paths, &current_state).unwrap();
    std::env::set_var("RECOVERY_SENTINEL", directory.path().join("recovered"));
    ensure_recovery_clear(&paths, &current_state).unwrap();
    std::env::remove_var("RECOVERY_SENTINEL");
    assert_eq!(
        fs::read_to_string(directory.path().join("recovered")).unwrap(),
        "uninstall"
    );
    let recovered = load_state(&paths).unwrap();
    assert!(!recovered.recovery_required);
    assert!(recovered.operation_log.is_none());
    assert_eq!(
        read_operation_log(&operation_path).unwrap().phase,
        "reverted"
    );
}

#[test]
fn committed_wal_converges_stale_gate_idempotently() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::model::Paths {
        data_home: directory.path().to_owned(),
        state_file: directory.path().join("state.toml"),
        lock_file: directory.path().join("lock"),
        repository_dir: directory.path().join("repository"),
        marketplaces_dir: directory.path().join("marketplaces"),
        backups_dir: directory.path().join("backups"),
        pending_plan: directory.path().join("pending-plan.json"),
        codex_home: directory.path().join("codex"),
    };
    let operation_path = operation_log_path(&paths.data_home, "committed-stale");
    let mut normalized = LocalState {
        schema_version: LOCAL_STATE_SCHEMA_VERSION,
        repository: RepositoryRef::parse("owner/repo", "main".to_owned()).unwrap(),
        device_id: "test".to_owned(),
        github_client_id: None,
        last_fetched_commit: None,
        fetched_repository_sha256: None,
        last_applied_commit: Some("target".to_owned()),
        managed_paths: Vec::new(),
        managed_agent_profiles: Vec::new(),
        latest_backup: Some("target-backup".to_owned()),
        provision_receipts: BTreeMap::new(),
        operation_log: None,
        recovery_required: false,
    };
    let target_digest = normalized_state_digest(&normalized).unwrap();
    let log = OperationLog {
        schema_version: 5,
        operation_id: "committed-stale".to_owned(),
        kind: "apply".to_owned(),
        phase: "committed".to_owned(),
        actions: Vec::new(),
        action_records: Vec::new(),
        backup: Some("target-backup".to_owned()),
        recovery_required: false,
        before_backup: Some("before-backup".to_owned()),
        target_state: Some(paths.state_file.to_string_lossy().into_owned()),
        before_state_digest: None,
        target_state_digest: Some(target_digest),
        supersedes: None,
        compensation_steps: Vec::new(),
    };
    create_operation_log(&operation_path, &log).unwrap();
    normalized.operation_log = Some(operation_path.to_string_lossy().into_owned());
    normalized.recovery_required = true;
    save_state(&paths, &normalized).unwrap();
    ensure_recovery_clear(&paths, &normalized).unwrap();
    let converged = load_state(&paths).unwrap();
    assert!(converged.operation_log.is_none());
    assert!(!converged.recovery_required);
    ensure_recovery_clear(&paths, &converged).unwrap();
    assert_eq!(
        load_state(&paths).unwrap().last_applied_commit,
        Some("target".to_owned())
    );
}

#[test]
fn schema5_rollback_recovery_restores_true_before_snapshot_without_actions() {
    let directory = tempfile::tempdir().unwrap();
    let paths = crate::model::Paths {
        data_home: directory.path().to_owned(),
        state_file: directory.path().join("state.toml"),
        lock_file: directory.path().join("lock"),
        repository_dir: directory.path().join("repository"),
        marketplaces_dir: directory.path().join("marketplaces"),
        backups_dir: directory.path().join("backups"),
        pending_plan: directory.path().join("pending-plan.json"),
        codex_home: directory.path().join("codex"),
    };
    fs::create_dir_all(&paths.codex_home).unwrap();
    fs::create_dir_all(paths.backups_dir.join("before")).unwrap();
    let before_state = LocalState {
        schema_version: LOCAL_STATE_SCHEMA_VERSION,
        repository: RepositoryRef::parse("owner/repo", "main".to_owned()).unwrap(),
        device_id: "before".to_owned(),
        github_client_id: None,
        last_fetched_commit: None,
        fetched_repository_sha256: None,
        last_applied_commit: Some("before".to_owned()),
        managed_paths: Vec::new(),
        managed_agent_profiles: Vec::new(),
        latest_backup: None,
        provision_receipts: BTreeMap::new(),
        operation_log: None,
        recovery_required: false,
    };
    fs::write(
        paths.backups_dir.join("before/state.toml"),
        toml::to_string(&before_state).unwrap(),
    )
    .unwrap();
    fs::write(paths.backups_dir.join("before/config.toml.absent"), b"").unwrap();
    fs::write(paths.backups_dir.join("before/AGENTS.md.absent"), b"").unwrap();
    fs::write(
        paths.backups_dir.join("before/plan.json"),
        serde_json::to_vec(&PendingPlan {
            id: "before".to_owned(),
            generated_at: "test".to_owned(),
            commit: "before".to_owned(),
            device_id: "before".to_owned(),
            base_config_sha256: String::new(),
            base_agents_sha256: String::new(),
            base_agent_profiles_sha256: String::new(),
            repository_sha256: String::new(),
            high_risk: false,
            changes: Vec::new(),
            managed_paths: Vec::new(),
            managed_agent_profiles: Vec::new(),
        })
        .unwrap(),
    )
    .unwrap();
    let operation_path = operation_log_path(&paths.data_home, "rollback-crash");
    let mut current = before_state.clone();
    current.device_id = "target".to_owned();
    current.operation_log = Some(operation_path.to_string_lossy().into_owned());
    current.recovery_required = true;
    save_state(&paths, &current).unwrap();
    create_operation_log(
        &operation_path,
        &OperationLog {
            schema_version: 5,
            operation_id: "rollback-crash".to_owned(),
            kind: "rollback".to_owned(),
            phase: "runtime_started".to_owned(),
            actions: Vec::new(),
            action_records: Vec::new(),
            backup: Some("target".to_owned()),
            recovery_required: false,
            before_backup: Some("before".to_owned()),
            target_state: None,
            before_state_digest: None,
            target_state_digest: None,
            supersedes: None,
            compensation_steps: Vec::new(),
        },
    )
    .unwrap();
    ensure_recovery_clear(&paths, &current).unwrap();
    let recovered = load_state(&paths).unwrap();
    assert_eq!(recovered.device_id, "before");
    assert!(recovered.operation_log.is_none());
    assert!(!recovered.recovery_required);
}

#[test]
fn rollback_runtime_restore_filters_shared_receipts_but_keeps_changed_targets() {
    let receipt = |plugin_id: &str, marker: &str| crate::model::ProvisionReceipt {
        schema_version: 2,
        plugin_id: plugin_id.to_owned(),
        artifact_digest: marker.to_owned(),
        artifact_root: marker.to_owned(),
        spec_sha256: marker.to_owned(),
        script_sha256: marker.to_owned(),
        dependencies_sha256: marker.to_owned(),
        setup_args: Vec::new(),
        uninstall_args: Vec::new(),
        windows_shell: String::new(),
        plugin_root: marker.to_owned(),
        script: marker.to_owned(),
        provisioned_at: String::new(),
    };
    let shared = receipt("shared@market", "shared");
    let current_changed = receipt("changed@market", "before");
    let target_changed = receipt("changed@market", "after");
    let mut current = BTreeMap::new();
    current.insert(shared.plugin_id.clone(), shared.clone());
    current.insert(current_changed.plugin_id.clone(), current_changed);
    let mut target = BTreeMap::new();
    target.insert(shared.plugin_id.clone(), shared);
    target.insert(target_changed.plugin_id.clone(), target_changed);
    let filtered = target_runtime_receipts_for_restore(&current, &target);
    assert!(!filtered.contains_key("shared@market"));
    assert_eq!(
        filtered.get("changed@market").unwrap().artifact_digest,
        "after"
    );
}
