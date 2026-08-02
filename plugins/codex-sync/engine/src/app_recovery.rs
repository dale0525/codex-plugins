fn ensure_recovery_clear(paths: &crate::model::Paths, state: &LocalState) -> Result<()> {
    // Read-only preflight over every checkpoint before any state, WAL, core,
    // or runtime mutation. This preserves bytes when multiple logs exist.
    let mut candidates = scan_operation_logs(&paths.data_home)?;
    if let Some(pointer) = state.operation_log.as_deref() {
        let pointer_path = PathBuf::from(pointer);
        let pointer_log = read_operation_log(&pointer_path)?;
        if operation_needs_recovery(&pointer_log)
            && !candidates.iter().any(|(path, _)| path == &pointer_path)
        {
            candidates.push((pointer_path, pointer_log));
        }
    }
    candidates.sort_by(|left, right| left.0.cmp(&right.0));
    candidates.dedup_by(|left, right| left.0 == right.0);
    if candidates.len() > 1 {
        let paths = candidates
            .iter()
            .map(|(path, _)| path.display().to_string())
            .collect::<Vec<_>>();
        anyhow::bail!("multiple unfinished operation checkpoints: {}", paths.join(", "));
    }
    let mut recovered_state = state.clone();
    if let Some((path, log)) = candidates.into_iter().next() {
        if log.schema_version < 3 {
            anyhow::bail!("Codex Sync has an unfinished operation checkpoint at {}; run rollback or repair before applying", path.display());
        }
        recover_completed_operation(paths, &path, &log)?;
        recovered_state = load_state(paths)?;
    }
    if let Some(pointer) = recovered_state.operation_log.as_deref() {
        let pointer_path = Path::new(pointer);
        let pointer_log = read_operation_log(pointer_path)?;
        if pointer_log.phase == "committed" {
            recovered_state = normalize_committed_state(paths, &recovered_state, &pointer_log)?;
        }
    }
    if recovered_state.recovery_required {
        anyhow::bail!("Codex Sync has a recovery-required operation; run rollback or repair before applying");
    }
    Ok(())
}

pub fn normalized_state_digest(state: &LocalState) -> Result<String> {
    let mut normalized = state.clone();
    normalized.operation_log = None;
    normalized.recovery_required = false;
    Ok(sha256(toml::to_string(&normalized)?.as_bytes()))
}

pub fn normalize_committed_state(
    paths: &crate::model::Paths,
    state: &LocalState,
    log: &OperationLog,
) -> Result<LocalState> {
    if log.schema_version < 5 {
        anyhow::bail!(
            "committed operation {} uses schema {}; refusing to clear recovery gate",
            log.operation_id,
            log.schema_version
        );
    }
    let expected = log.target_state_digest.as_deref().with_context(|| {
        format!(
            "committed operation {} has no target state digest; refusing to clear recovery gate",
            log.operation_id
        )
    })?;
    let actual = normalized_state_digest(state)?;
    if actual != expected {
        anyhow::bail!(
            "committed operation {} final state digest mismatch (expected {}, got {}); refusing to clear recovery gate",
            log.operation_id,
            expected,
            actual
        );
    }
    let mut normalized = state.clone();
    normalized.operation_log = None;
    normalized.recovery_required = false;
    crate::storage::save_state(paths, &normalized)?;
    Ok(normalized)
}

fn target_runtime_receipts_for_restore(
    current: &std::collections::BTreeMap<String, crate::model::ProvisionReceipt>,
    target: &std::collections::BTreeMap<String, crate::model::ProvisionReceipt>,
) -> std::collections::BTreeMap<String, crate::model::ProvisionReceipt> {
    target
        .iter()
        .filter(|(plugin_id, receipt)| current.get(*plugin_id) != Some(*receipt))
        .map(|(plugin_id, receipt)| (plugin_id.clone(), receipt.clone()))
        .collect()
}

fn recover_completed_operation(paths: &crate::model::Paths, operation_path: &Path, log: &OperationLog) -> Result<()> {
    if log.phase == "committed" && log.recovery_required {
        anyhow::bail!(
            "committed operation {} has an unresolved durability/recovery gate; manual recovery is required",
            log.operation_id
        );
    }
    if log.kind == "rollback" && log.schema_version < 5 {
        anyhow::bail!(
            "schema<5 unfinished rollback {} has no true before snapshot; manual recovery is required",
            log.operation_id
        );
    }
    if log.schema_version == 3 && log.phase == "compensating" {
        anyhow::bail!("schema-3 operation {} was already compensating; manual recovery is required", log.operation_id);
    }
    if log.action_records.iter().any(|action| {
        matches!(
            action.status,
            ActionStatus::Running | ActionStatus::ManualRequired | ActionStatus::RecoveryRequired
        )
    }) {
        let mut blocked = log.clone();
        blocked.phase = "manual-required".to_owned();
        blocked.recovery_required = true;
        for action in &mut blocked.action_records {
            if matches!(action.status, ActionStatus::Running) {
                action.status = ActionStatus::ManualRequired;
                action.phase = "manual-required".to_owned();
                action.message = Some("action outcome is ambiguous after restart; verify runtime manually before rollback".to_owned());
            }
        }
        write_operation_log(operation_path, &blocked)?;
        anyhow::bail!("operation {} contains an ambiguous/manual-required action; manual recovery is required", log.operation_id);
    }
    if log.compensation_steps.iter().any(|step| {
        matches!(
            step.status,
            crate::provision::CompensationStatus::Running
                | crate::provision::CompensationStatus::ManualRequired
        )
    }) {
        let mut blocked = log.clone();
        blocked.phase = "manual-required".to_owned();
        blocked.recovery_required = true;
        for step in &mut blocked.compensation_steps {
            if matches!(
                step.status,
                crate::provision::CompensationStatus::Running
                    | crate::provision::CompensationStatus::ManualRequired
            ) {
                step.status = crate::provision::CompensationStatus::ManualRequired;
                step.message = Some("compensation outcome is ambiguous after restart; verify runtime manually before rollback".to_owned());
            }
        }
        write_operation_log(operation_path, &blocked)?;
        anyhow::bail!("operation {} contains an ambiguous compensation step; manual recovery is required", log.operation_id);
    }
    let backup_value = log.before_backup.clone().or_else(|| {
        log.backup.clone().map(|name| paths.backups_dir.join(name).to_string_lossy().into_owned())
    }).context("unfinished operation has no durable backup identity")?;
    let backup = if log.schema_version >= 5 {
        let name = Path::new(&backup_value)
            .file_name()
            .filter(|_| !Path::new(&backup_value).is_absolute())
            .context("schema-5 before snapshot must be a backup basename")?
            .to_string_lossy();
        if name.contains(['/', '\\']) || name != backup_value {
            anyhow::bail!("schema-5 before snapshot must be a safe backup basename");
        }
        paths.backups_dir.join(name.as_ref())
    } else {
        PathBuf::from(&backup_value)
    };
    if !backup.is_dir() {
        anyhow::bail!("unfinished operation backup does not exist: {}", backup.display());
    }
    let mut recorder = OperationRecorder::new(operation_path.to_owned(), log.clone())?;
    recorder.set_phase("compensating")?;
    recorder.materialize_compensation_plan(&operations_from_log(&recorder.log))?;
    restore_backup(paths, &backup)?;
    let operations = operations_from_log(&recorder.log);
    if let Err(error) = compensate_operations_recorded(&operations, Some(&mut recorder)) {
        recorder.log.phase = "recovery_required".to_owned();
        recorder.log.recovery_required = true;
        recorder.persist()?;
        anyhow::bail!("completed runtime recovery failed: {error:#}");
    }
    recorder.set_phase("reverted")?;
    let mut state = load_state(paths)?;
    state.operation_log = None;
    state.recovery_required = false;
    save_state(paths, &state)
}

fn operations_from_log(log: &OperationLog) -> Vec<RuntimeOperation> {
    log.action_records
        .iter()
        .filter(|action| matches!(action.status, ActionStatus::Completed | ActionStatus::Succeeded))
        .map(|action| RuntimeOperation {
            plugin_id: action.plugin_id.clone(),
            receipt: action.after_receipt.clone().unwrap_or_else(|| action.receipt.clone()),
            previous: action.before_receipt.clone().or_else(|| action.previous.clone()),
            uninstall: action.uninstall,
            action_id: Some(action.action_id.clone()),
        })
        .collect()
}
