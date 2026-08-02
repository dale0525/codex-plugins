use super::*;

#[test]
fn safe_child_rejects_parent_traversal() {
    let temporary = tempfile::tempdir().unwrap();
    assert!(safe_child(temporary.path(), "../outside").is_err());
}

#[test]
fn windows_provisioners_bypass_only_process_execution_policy() {
    let (launcher, arguments) = launcher_for(true, WindowsShell::Pwsh).unwrap();
    assert_eq!(launcher, "pwsh");
    assert_eq!(
        arguments.iter().map(String::as_str).collect::<Vec<_>>(),
        vec![
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
        ]
    );
}

#[test]
fn windows_shell_defaults_to_pwsh_and_supports_ps5() {
    let default: ProvisionSpec = serde_json::from_str(
        r#"{"schema_version":1,"risk":"high","posix_script":"a","windows_script":"b"}"#,
    )
    .unwrap();
    assert_eq!(default.windows_shell, WindowsShell::Pwsh);
    let ps5: ProvisionSpec = serde_json::from_str(
        r#"{"schema_version":1,"risk":"high","posix_script":"a","windows_script":"b","windows_shell":"windows-powershell"}"#,
    )
    .unwrap();
    assert_eq!(ps5.windows_shell, WindowsShell::WindowsPowershell);
    assert!(serde_json::from_str::<ProvisionSpec>(
        r#"{"schema_version":1,"risk":"high","posix_script":"a","windows_script":"b","windows_shell":"cmd"}"#
    )
    .is_err());
}

#[test]
fn windows_powershell_prefers_system_root_then_path_fallback() {
    let temporary = tempfile::tempdir().unwrap();
    let executable = temporary
        .path()
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    fs::create_dir_all(executable.parent().unwrap()).unwrap();
    fs::write(&executable, b"fake").unwrap();
    assert_eq!(
        resolve_windows_powershell_from(Some(temporary.path().as_os_str())),
        executable.to_string_lossy()
    );
    assert_eq!(resolve_windows_powershell_from(None), "powershell.exe");
}

#[test]
fn execution_rejects_script_content_drift() {
    let temporary = tempfile::tempdir().unwrap();
    let script = temporary.path().join("provision.sh");
    fs::write(&script, "exit 0\n").unwrap();
    let spec: ProvisionSpec = serde_json::from_str(
        r#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1"}"#,
    )
    .unwrap();
    let dependencies = directory_sha256(temporary.path()).unwrap();
    let result = run_script(
        "test@market",
        temporary.path(),
        &script,
        &spec,
        &[],
        &"00".repeat(32),
        &dependencies,
    );
    assert!(result.unwrap_err().to_string().contains("script changed"));
}

#[test]
fn execution_rejects_dependency_drift() {
    let temporary = tempfile::tempdir().unwrap();
    let script = temporary.path().join("provision.sh");
    fs::write(&script, "exit 0\n").unwrap();
    let spec: ProvisionSpec = serde_json::from_str(
        r#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1"}"#,
    )
    .unwrap();
    let script_hash = hex::encode(Sha256::digest(fs::read(&script).unwrap()));
    let dependencies = directory_sha256(temporary.path()).unwrap();
    fs::write(temporary.path().join("helper.sh"), "echo helper\n").unwrap();
    let result = run_script(
        "test@market",
        temporary.path(),
        &script,
        &spec,
        &[],
        &script_hash,
        &dependencies,
    );
    assert!(result
        .unwrap_err()
        .to_string()
        .contains("dependencies changed"));
}

#[test]
fn new_auto_provisioner_is_deferred_until_reconcile() {
    let plugin = PluginSpec {
        id: "new-plugin@new-market".to_owned(),
        enabled: true,
        auto_provision: true,
    };
    assert!(validate_auto_provisioners(&[plugin], &std::collections::BTreeMap::new()).is_ok());
}

#[test]
fn compensation_reverses_multi_and_removal_operations() {
    let temporary = tempfile::tempdir().unwrap();
    let log = temporary.path().join("operations.log");
    std::env::set_var("PROVISION_TEST_LOG", &log);
    let mut receipts = Vec::new();
    for (index, name) in ["first", "second"].into_iter().enumerate() {
        let root = temporary.path().join(name);
        fs::create_dir_all(root.join(".codex-sync")).unwrap();
        let script = root.join("provision.sh");
        fs::write(
            &script,
            "#!/bin/sh\nprintf '%s-%s\\n' \"$1\" \"$(basename \"$PWD\")\" >> \"$PROVISION_TEST_LOG\"\n",
        )
        .unwrap();
        let spec_bytes = br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#;
        fs::write(root.join(".codex-sync/provision.json"), spec_bytes).unwrap();
        let script_sha256 = hex::encode(Sha256::digest(fs::read(&script).unwrap()));
        let dependencies_sha256 = directory_sha256(&root).unwrap();
        receipts.push(ProvisionReceipt {
            schema_version: 2,
            plugin_id: format!("{name}@market"),
            artifact_digest: crate::artifact::digest(&root).unwrap(),
            artifact_root: root.to_string_lossy().into_owned(),
            spec_sha256: hex::encode(Sha256::digest(spec_bytes)),
            script_sha256,
            dependencies_sha256,
            setup_args: vec!["setup".to_owned()],
            uninstall_args: vec!["uninstall".to_owned()],
            windows_shell: "Pwsh".to_owned(),
            plugin_root: root.to_string_lossy().into_owned(),
            script: script.to_string_lossy().into_owned(),
            provisioned_at: format!("{index}"),
        });
    }
    let operations = vec![
        RuntimeOperation {
            plugin_id: receipts[0].plugin_id.clone(),
            receipt: receipts[0].clone(),
            previous: None,
            uninstall: false,
            action_id: None,
        },
        RuntimeOperation {
            plugin_id: receipts[1].plugin_id.clone(),
            receipt: receipts[1].clone(),
            previous: None,
            uninstall: false,
            action_id: None,
        },
        RuntimeOperation {
            plugin_id: receipts[0].plugin_id.clone(),
            receipt: receipts[0].clone(),
            previous: Some(receipts[0].clone()),
            uninstall: true,
            action_id: None,
        },
    ];
    compensate_operations(&operations).unwrap();
    let output = fs::read_to_string(log).unwrap();
    assert!(output.contains("setup-first"));
    assert!(output.contains("uninstall-second"));
    std::env::remove_var("PROVISION_TEST_LOG");
}

fn receipt_for(root: &Path, plugin_id: &str) -> ProvisionReceipt {
    let script = root.join("provision.sh");
    let spec_bytes = br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#;
    ProvisionReceipt {
        schema_version: 2,
        plugin_id: plugin_id.to_owned(),
        artifact_digest: crate::artifact::digest(root).unwrap(),
        artifact_root: root.to_string_lossy().into_owned(),
        spec_sha256: hex::encode(Sha256::digest(spec_bytes)),
        script_sha256: hex::encode(Sha256::digest(fs::read(&script).unwrap())),
        dependencies_sha256: directory_sha256(root).unwrap(),
        setup_args: vec!["setup".to_owned()],
        uninstall_args: vec!["uninstall".to_owned()],
        windows_shell: "Pwsh".to_owned(),
        plugin_root: root.to_string_lossy().into_owned(),
        script: script.to_string_lossy().into_owned(),
        provisioned_at: "test".to_owned(),
    }
}

fn legacy_receipt(root: &Path, plugin_id: &str) -> ProvisionReceipt {
    let script = root.join("provision.sh");
    let spec = fs::read(root.join(".codex-sync/provision.json")).unwrap();
    ProvisionReceipt {
        schema_version: 1,
        plugin_id: plugin_id.to_owned(),
        artifact_digest: String::new(),
        artifact_root: String::new(),
        spec_sha256: hex::encode(Sha256::digest(&spec)),
        script_sha256: hex::encode(Sha256::digest(fs::read(&script).unwrap())),
        dependencies_sha256: directory_sha256(script.parent().unwrap()).unwrap(),
        setup_args: vec!["setup".to_owned()],
        uninstall_args: vec!["uninstall".to_owned()],
        windows_shell: "Pwsh".to_owned(),
        plugin_root: root.to_string_lossy().into_owned(),
        script: script.to_string_lossy().into_owned(),
        provisioned_at: "legacy".to_owned(),
    }
}

fn legacy_root(temp: &tempfile::TempDir, name: &str, spec: &[u8]) -> PathBuf {
    let root = temp.path().join(name);
    fs::create_dir_all(root.join(".codex-sync")).unwrap();
    fs::write(root.join(".codex-sync/provision.json"), spec).unwrap();
    fs::write(root.join("provision.sh"), "#!/bin/sh\nexit 0\n").unwrap();
    root
}

#[test]
fn legacy_receipts_prevalidate_globally_before_any_mutation() {
    let temp = tempfile::tempdir().unwrap();
    let spec = br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1"}"#;
    let valid_root = legacy_root(&temp, "valid", spec);
    let invalid_root = legacy_root(&temp, "invalid", spec);
    let valid = legacy_receipt(&valid_root, "valid@market");
    let mut invalid = legacy_receipt(&invalid_root, "invalid@market");
    invalid.script_sha256.clear();
    let mut receipts = std::collections::BTreeMap::new();
    receipts.insert(valid.plugin_id.clone(), valid);
    receipts.insert(invalid.plugin_id.clone(), invalid);
    let before = receipts.clone();
    assert!(migrate_receipts(&mut receipts, temp.path()).is_err());
    assert_eq!(receipts, before);
    assert!(!temp.path().join("provision-artifacts").exists());
}

#[test]
fn legacy_receipt_rejects_script_traversal_and_wrong_spec_contract() {
    let temp = tempfile::tempdir().unwrap();
    let traversal_spec = br#"{"schema_version":1,"risk":"high","posix_script":"../escape.sh","windows_script":"provision.ps1"}"#;
    let traversal_root = legacy_root(&temp, "traversal", traversal_spec);
    let mut traversal = legacy_receipt(&traversal_root, "traversal@market");
    traversal.script = traversal_root
        .join("../escape.sh")
        .to_string_lossy()
        .into_owned();
    let mut receipts = std::collections::BTreeMap::new();
    receipts.insert(traversal.plugin_id.clone(), traversal);
    assert!(migrate_receipts(&mut receipts, temp.path()).is_err());

    let wrong_spec = br#"{"schema_version":2,"risk":"low","posix_script":"provision.sh","windows_script":"provision.ps1"}"#;
    let wrong_root = legacy_root(&temp, "wrong", wrong_spec);
    let wrong = legacy_receipt(&wrong_root, "wrong@market");
    receipts.clear();
    receipts.insert(wrong.plugin_id.clone(), wrong);
    assert!(migrate_receipts(&mut receipts, temp.path()).is_err());
}

#[test]
fn missing_artifact_root_receipt_is_rejected_before_uninstall() {
    let temporary = tempfile::tempdir().unwrap();
    let missing = temporary.path().join("removed-artifact");
    let receipt = ProvisionReceipt {
        schema_version: 2,
        plugin_id: "removed@market".to_owned(),
        artifact_digest: "00".repeat(32),
        artifact_root: missing.to_string_lossy().into_owned(),
        spec_sha256: "00".repeat(32),
        script_sha256: "00".repeat(32),
        dependencies_sha256: "00".repeat(32),
        setup_args: vec!["setup".to_owned()],
        uninstall_args: vec!["uninstall".to_owned()],
        windows_shell: "Pwsh".to_owned(),
        plugin_root: missing.to_string_lossy().into_owned(),
        script: missing.join("provision.sh").to_string_lossy().into_owned(),
        provisioned_at: "test".to_owned(),
    };
    let mut receipts = std::collections::BTreeMap::new();
    receipts.insert(receipt.plugin_id.clone(), receipt);
    let error =
        run_uninstallers(&["removed@market".to_owned()], &receipts, &mut Vec::new()).unwrap_err();
    assert!(error.to_string().contains("missing runtime"));
}

#[cfg(unix)]
#[test]
fn uninstall_uses_artifact_after_source_root_removed() {
    let temporary = tempfile::tempdir().unwrap();
    let source = temporary.path().join("marketplace-plugin");
    fs::create_dir_all(source.join(".codex-sync")).unwrap();
    fs::write(
        source.join(".codex-sync/provision.json"),
        br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#,
    )
    .unwrap();
    fs::write(
        source.join("provision.sh"),
        "#!/bin/sh\nprintf '%s' uninstall > \"${PROVISION_ARTIFACT_SENTINEL:?}\"\n",
    )
    .unwrap();
    let artifact = crate::artifact::materialize(&source, temporary.path()).unwrap();
    let receipt = receipt_for(&artifact.root, "source-removed@market");
    fs::remove_dir_all(&source).unwrap();
    let sentinel = temporary.path().join("artifact-uninstall");
    std::env::set_var("PROVISION_ARTIFACT_SENTINEL", &sentinel);
    let mut receipts = std::collections::BTreeMap::new();
    receipts.insert(receipt.plugin_id.clone(), receipt);
    run_uninstallers(
        &["source-removed@market".to_owned()],
        &receipts,
        &mut Vec::new(),
    )
    .unwrap();
    std::env::remove_var("PROVISION_ARTIFACT_SENTINEL");
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "uninstall");
}

#[test]
fn unknown_started_operation_is_a_recovery_checkpoint() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("operation.json");
    fs::write(
        &path,
        serde_json::to_vec(&OperationLog {
            schema_version: 1,
            operation_id: "apply-1".to_owned(),
            kind: "apply".to_owned(),
            phase: "runtime_started".to_owned(),
            actions: vec!["tool@market:setup".to_owned()],
            action_records: Vec::new(),
            backup: Some("backup-1".to_owned()),
            recovery_required: false,
            before_backup: None,
            target_state: None,
            before_state_digest: None,
            target_state_digest: None,
            supersedes: None,
            compensation_steps: Vec::new(),
        })
        .unwrap(),
    )
    .unwrap();
    let log = read_operation_log(&path).unwrap();
    assert!(operation_needs_recovery(&log));
    fs::write(
        &path,
        br#"{"schema_version":1,"operation_id":"apply-1","kind":"apply","phase":"future-phase","actions":[],"recovery_required":false}"#,
    )
    .unwrap();
    assert!(read_operation_log(&path).is_err());
}

#[test]
fn schema3_duplicate_action_ids_are_rejected() {
    let temp = tempfile::tempdir().unwrap();
    let root = legacy_root(
        &temp,
        "duplicate",
        br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#,
    );
    let receipt = receipt_for(&root, "tool@market");
    let action = |id: &str| OperationAction {
        action_id: id.to_owned(),
        plugin_id: receipt.plugin_id.clone(),
        receipt: receipt.clone(),
        previous: None,
        uninstall: false,
        kind: "provision".to_owned(),
        status: ActionStatus::Completed,
        message: None,
        operation_kind: "setup".to_owned(),
        phase: "completed".to_owned(),
        before_receipt: None,
        after_receipt: Some(receipt.clone()),
    };
    let path = temp.path().join("duplicate.json");
    fs::write(
        &path,
        serde_json::to_vec(&OperationLog {
            schema_version: 3,
            operation_id: "duplicate".to_owned(),
            kind: "apply".to_owned(),
            phase: "commit-prepared".to_owned(),
            actions: Vec::new(),
            action_records: vec![action("same"), action("same")],
            backup: None,
            recovery_required: false,
            before_backup: None,
            target_state: None,
            before_state_digest: None,
            target_state_digest: None,
            supersedes: None,
            compensation_steps: Vec::new(),
        })
        .unwrap(),
    )
    .unwrap();
    assert!(read_operation_log(&path).is_err());
}

#[test]
fn runtime_succeeded_is_not_a_terminal_wal_phase() {
    let log = OperationLog {
        schema_version: 3,
        operation_id: "op".to_owned(),
        kind: "apply".to_owned(),
        phase: "runtime_succeeded".to_owned(),
        actions: Vec::new(),
        action_records: Vec::new(),
        backup: None,
        recovery_required: false,
        before_backup: None,
        target_state: None,
        before_state_digest: None,
        target_state_digest: None,
        supersedes: None,
        compensation_steps: Vec::new(),
    };
    assert!(operation_needs_recovery(&log));
}

#[test]
fn operation_ids_are_unique_for_repeated_hints() {
    assert_ne!(
        new_operation_id("apply", "same"),
        new_operation_id("apply", "same")
    );
}

#[test]
fn operation_log_creation_refuses_collision() {
    let temp = tempfile::tempdir().unwrap();
    let log = OperationLog {
        schema_version: 3,
        operation_id: "collision".to_owned(),
        kind: "apply".to_owned(),
        phase: "checkpointed".to_owned(),
        actions: Vec::new(),
        action_records: Vec::new(),
        backup: None,
        recovery_required: false,
        before_backup: None,
        target_state: None,
        before_state_digest: None,
        target_state_digest: None,
        supersedes: None,
        compensation_steps: Vec::new(),
    };
    let path = temp.path().join("operation.json");
    create_operation_log(&path, &log).unwrap();
    assert!(create_operation_log(&path, &log).is_err());
}

#[cfg(unix)]
#[test]
fn target_receipt_restore_failure_is_reported() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("artifact");
    fs::create_dir_all(root.join(".codex-sync")).unwrap();
    fs::write(
        root.join(".codex-sync/provision.json"),
        br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#,
    )
    .unwrap();
    fs::write(root.join("provision.sh"), "#!/bin/sh\nexit 17\n").unwrap();
    let receipt = receipt_for(&root, "restore-failure@market");
    let mut receipts = std::collections::BTreeMap::new();
    receipts.insert(receipt.plugin_id.clone(), receipt);
    let error = restore_provisioners(&receipts).unwrap_err();
    assert!(error.to_string().contains("restore-failure@market"));
    assert!(error.to_string().contains("runtime restoration failed"));
}

#[cfg(unix)]
#[test]
fn recorded_restore_nonzero_keeps_manual_wal_and_partial_side_effect() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("artifact");
    fs::create_dir_all(root.join(".codex-sync")).unwrap();
    fs::write(
        root.join(".codex-sync/provision.json"),
        br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#,
    )
    .unwrap();
    let sentinel = temporary.path().join("sentinel");
    fs::write(
        root.join("provision.sh"),
        format!(
            "#!/bin/sh\nprintf side-effect > '{}'\nexit 17\n",
            sentinel.display()
        ),
    )
    .unwrap();
    let receipt = receipt_for(&root, "manual-restore@market");
    let mut receipts = std::collections::BTreeMap::new();
    receipts.insert(receipt.plugin_id.clone(), receipt);
    let operation_path = temporary.path().join("operation.json");
    let mut recorder = OperationRecorder::new(
        operation_path.clone(),
        OperationLog {
            schema_version: 5,
            operation_id: "manual-restore".to_owned(),
            kind: "rollback".to_owned(),
            phase: "runtime_started".to_owned(),
            actions: Vec::new(),
            action_records: Vec::new(),
            backup: None,
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
    let error =
        restore_provisioners_recorded(&receipts, &mut Vec::new(), Some(&mut recorder)).unwrap_err();
    assert!(error.to_string().contains("runtime restoration failed"));
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "side-effect");
    let log = read_operation_log(&operation_path).unwrap();
    assert!(log.recovery_required);
    assert_eq!(log.phase, "manual-required");
    assert!(matches!(
        log.action_records.first().map(|action| &action.status),
        Some(ActionStatus::ManualRequired)
    ));
}

#[cfg(unix)]
#[test]
fn recorded_schema2_uninstall_nonzero_persists_manual_wal_before_return() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("artifact");
    fs::create_dir_all(root.join(".codex-sync")).unwrap();
    fs::write(
        root.join(".codex-sync/provision.json"),
        br#"{"schema_version":1,"risk":"high","posix_script":"provision.sh","windows_script":"provision.ps1","arguments":["setup"]}"#,
    )
    .unwrap();
    let sentinel = temporary.path().join("uninstall-sentinel");
    fs::write(
        root.join("provision.sh"),
        format!(
            "#!/bin/sh\nprintf uninstall > '{}'\nexit 17\n",
            sentinel.display()
        ),
    )
    .unwrap();
    let receipt = receipt_for(&root, "manual-uninstall@market");
    let mut receipts = std::collections::BTreeMap::new();
    receipts.insert(receipt.plugin_id.clone(), receipt);
    let operation_path = temporary.path().join("operation.json");
    let mut recorder = OperationRecorder::new(
        operation_path.clone(),
        OperationLog {
            schema_version: 5,
            operation_id: "manual-uninstall".to_owned(),
            kind: "apply".to_owned(),
            phase: "runtime_started".to_owned(),
            actions: Vec::new(),
            action_records: Vec::new(),
            backup: None,
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
    let error = run_uninstallers_recorded(
        &["manual-uninstall@market".to_owned()],
        &receipts,
        &mut Vec::new(),
        Some(&mut recorder),
    )
    .unwrap_err();
    assert!(error.to_string().contains("manual-uninstall@market"));
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "uninstall");
    let log = read_operation_log(&operation_path).unwrap();
    assert!(log.recovery_required);
    assert_eq!(log.phase, "manual-required");
    assert!(matches!(
        log.action_records.first().map(|action| &action.status),
        Some(ActionStatus::ManualRequired)
    ));
}
