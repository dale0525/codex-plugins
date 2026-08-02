use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use chrono::Utc;

use crate::model::{LocalState, PendingPlan};
use crate::provision::{
    write_operation_log, ActionStatus, CompensationStatus, CompensationStep, OperationAction,
    OperationLog, RuntimeOperation,
};

/// Durable recorder shared by apply and rollback. Every external provisioner
/// action is written as intent (and then running) before its process is
/// spawned; result checkpoints are persisted immediately afterwards.
pub struct OperationRecorder {
    path: PathBuf,
    pub log: OperationLog,
}

impl OperationRecorder {
    pub fn new(path: PathBuf, log: OperationLog) -> Result<Self> {
        let mut log = log;
        // Any newly written WAL is authoritative schema 5. Legacy schema 2/3
        // logs remain readable, but are upgraded at the next durable write.
        log.schema_version = 5;
        let recorder = Self { path, log };
        recorder.persist()?;
        Ok(recorder)
    }

    pub fn persist(&self) -> Result<()> {
        write_operation_log(&self.path, &self.log)
    }

    pub fn set_phase(&mut self, phase: &str) -> Result<()> {
        self.log.phase = phase.to_owned();
        self.persist()
    }

    pub fn intent(&mut self, operation: &RuntimeOperation) -> Result<String> {
        let action_id = format!(
            "{}-a{}",
            self.log.operation_id,
            self.log.action_records.len() + 1
        );
        self.log.action_records.push(OperationAction {
            action_id: action_id.clone(),
            plugin_id: operation.plugin_id.clone(),
            receipt: operation.receipt.clone(),
            previous: operation.previous.clone(),
            uninstall: operation.uninstall,
            kind: if operation.uninstall {
                "uninstall"
            } else {
                "provision"
            }
            .to_owned(),
            status: ActionStatus::Intent,
            message: None,
            operation_kind: if operation.uninstall {
                "uninstall"
            } else {
                "setup"
            }
            .to_owned(),
            phase: "intent".to_owned(),
            before_receipt: operation.previous.clone(),
            after_receipt: (!operation.uninstall).then(|| operation.receipt.clone()),
        });
        self.log.actions.push(format!(
            "{}:{}",
            operation.plugin_id,
            if operation.uninstall {
                "uninstall"
            } else {
                "setup"
            }
        ));
        self.persist()?;
        Ok(action_id)
    }

    fn update_status(
        &mut self,
        action_id: &str,
        status: ActionStatus,
        message: Option<String>,
    ) -> Result<()> {
        let action = self
            .log
            .action_records
            .iter_mut()
            .find(|action| action.action_id == action_id)
            .context("operation action ID not found")?;
        action.status = status;
        action.message = message;
        action.phase = match &action.status {
            ActionStatus::Intent => "intent",
            ActionStatus::Running => "running",
            ActionStatus::Succeeded | ActionStatus::Completed => "completed",
            ActionStatus::Compensated => "compensated",
            ActionStatus::RecoveryRequired | ActionStatus::ManualRequired => "manual-required",
            ActionStatus::Failed => "failed",
            ActionStatus::NotRun => "not-run",
        }
        .to_owned();
        self.persist()
    }

    pub fn running(&mut self, action_id: &str) -> Result<()> {
        self.update_status(action_id, ActionStatus::Running, None)
    }

    pub fn succeeded(&mut self, action_id: &str, message: String) -> Result<()> {
        self.update_status(action_id, ActionStatus::Completed, Some(message))
    }

    #[allow(dead_code)]
    pub fn compensated(&mut self, action_id: &str) -> Result<()> {
        self.update_status(action_id, ActionStatus::Compensated, None)
    }

    pub fn recovery_required(&mut self, action_id: &str, message: String) -> Result<()> {
        self.update_status(action_id, ActionStatus::ManualRequired, Some(message))?;
        self.log.phase = "manual-required".to_owned();
        self.log.recovery_required = true;
        self.persist()
    }

    pub fn has_blocked_actions(&self) -> bool {
        self.log.action_records.iter().any(|action| {
            matches!(
                action.status,
                ActionStatus::Running
                    | ActionStatus::ManualRequired
                    | ActionStatus::RecoveryRequired
            )
        })
    }

    pub fn has_blocked_compensation(&self) -> bool {
        self.log.compensation_steps.iter().any(|step| {
            matches!(
                step.status,
                CompensationStatus::Running | CompensationStatus::ManualRequired
            )
        })
    }

    pub fn status(&self, action_id: &str) -> Option<&ActionStatus> {
        self.log
            .action_records
            .iter()
            .find(|action| action.action_id == action_id)
            .map(|action| &action.status)
    }

    /// Materialize the complete reverse-runtime plan exactly once.  The plan
    /// is persisted before core restoration or any compensation process starts.
    pub fn materialize_compensation_plan(&mut self, operations: &[RuntimeOperation]) -> Result<()> {
        if !self.log.compensation_steps.is_empty() {
            return Ok(());
        }
        let mut steps = Vec::new();
        for (index, operation) in operations.iter().rev().enumerate() {
            let Some(action_id) = operation.action_id.clone() else {
                continue;
            };
            if !matches!(
                self.status(&action_id),
                Some(ActionStatus::Completed | ActionStatus::Succeeded)
            ) {
                continue;
            }
            steps.push(CompensationStep {
                step_id: format!("{}-c{}", self.log.operation_id, index + 1),
                action_id,
                plugin_id: operation.plugin_id.clone(),
                receipt: operation.receipt.clone(),
                previous: operation.previous.clone(),
                uninstall: operation.uninstall,
                status: CompensationStatus::Intent,
                message: None,
            });
        }
        self.log.compensation_steps = steps;
        self.persist()
    }

    #[allow(dead_code)]
    pub fn compensation_status(&self, step_id: &str) -> Option<&CompensationStatus> {
        self.log
            .compensation_steps
            .iter()
            .find(|step| step.step_id == step_id)
            .map(|step| &step.status)
    }

    pub fn set_compensation_status(
        &mut self,
        step_id: &str,
        status: CompensationStatus,
        message: Option<String>,
    ) -> Result<()> {
        let step = self
            .log
            .compensation_steps
            .iter_mut()
            .find(|step| step.step_id == step_id)
            .context("compensation step ID not found")?;
        step.status = status;
        step.message = message;
        self.persist()
    }
}
use crate::profiles::current_profile_bytes;
use crate::reconcile::{
    add_local_marketplace, installed_plugins, marketplace_roots, remove_marketplace,
    restore_installed_plugins, InstalledPlugin,
};
use crate::storage::{atomic_write, copy_tree, read_json, read_toml, write_json};

pub fn create_backup(
    paths: &crate::model::Paths,
    state: &LocalState,
    plan: &PendingPlan,
) -> Result<PathBuf> {
    let name = format!(
        "{}-{}-{}",
        Utc::now().format("%Y%m%dT%H%M%SZ"),
        &plan.commit[..plan.commit.len().min(12)],
        plan.id
    );
    let backup = paths.backups_dir.join(name);
    fs::create_dir_all(&backup)?;
    for file in ["config.toml", "AGENTS.md"] {
        let source = paths.codex_home.join(file);
        if source.exists() {
            fs::copy(&source, backup.join(file))?;
        } else {
            atomic_write(&backup.join(format!("{file}.absent")), b"")?;
        }
    }
    let profile_backup = backup.join("agent-profiles");
    fs::create_dir_all(&profile_backup)?;
    let profile_names: BTreeSet<_> = state
        .managed_agent_profiles
        .iter()
        .chain(plan.managed_agent_profiles.iter())
        .cloned()
        .collect();
    for name in profile_names {
        match current_profile_bytes(&paths.codex_home.join("agents"), &name)? {
            Some(bytes) => atomic_write(&profile_backup.join(format!("{name}.toml")), &bytes)?,
            None => atomic_write(&profile_backup.join(format!("{name}.absent")), b"")?,
        }
    }
    let state_text = toml::to_string_pretty(state)?;
    atomic_write(&backup.join("state.toml"), state_text.as_bytes())?;
    let plugin_state = installed_plugins()?;
    write_json(&backup.join("plugins.json"), &plugin_state)?;
    let marketplace_state = marketplace_roots()?;
    write_json(&backup.join("marketplaces.json"), &marketplace_state)?;
    let affected_marketplaces: BTreeSet<_> = plan
        .changes
        .iter()
        .filter(|change| change.kind == "marketplace")
        .map(|change| change.target.as_str())
        .collect();
    for (name, root) in &marketplace_state {
        if affected_marketplaces.contains(name.as_str())
            && root.is_dir()
            && (root.starts_with(&paths.codex_home) || root.starts_with(&paths.data_home))
        {
            copy_tree(root, &backup.join("marketplace-snapshots").join(name))?;
        }
    }
    write_json(&backup.join("plan.json"), plan)?;
    Ok(backup)
}

/// Snapshot the currently active state before a rollback mutates anything.
/// The resulting basename is intentionally distinct from the rollback target
/// and is the only `before_backup` identity accepted by schema-5 WALs.
pub fn create_rollback_before_backup(
    paths: &crate::model::Paths,
    state: &LocalState,
    target_backup: &Path,
    operation_id: &str,
) -> Result<PathBuf> {
    let target_name = target_backup
        .file_name()
        .context("rollback target backup has no basename")?
        .to_string_lossy();
    let name = format!("rollback-before-{operation_id}");
    if name == target_name || name.contains(['/', '\\']) {
        anyhow::bail!("rollback before snapshot must differ from target backup")
    }
    let target_plan_path = target_backup.join("plan.json");
    let mut plan: PendingPlan = if target_plan_path.is_file() {
        read_json(&target_plan_path)?
    } else {
        PendingPlan {
            id: name.clone(),
            generated_at: Utc::now().to_rfc3339(),
            commit: state
                .last_applied_commit
                .clone()
                .unwrap_or_else(|| "rollback".to_owned()),
            device_id: state.device_id.clone(),
            base_config_sha256: String::new(),
            base_agents_sha256: String::new(),
            base_agent_profiles_sha256: String::new(),
            repository_sha256: String::new(),
            high_risk: false,
            changes: Vec::new(),
            managed_paths: state.managed_paths.clone(),
            managed_agent_profiles: state.managed_agent_profiles.clone(),
        }
    };
    plan.id = name.clone();
    plan.generated_at = Utc::now().to_rfc3339();
    plan.device_id = state.device_id.clone();
    plan.managed_paths = state.managed_paths.clone();
    plan.managed_agent_profiles = state.managed_agent_profiles.clone();
    let before = paths.backups_dir.join(&name);
    if before == target_backup {
        anyhow::bail!("rollback before snapshot must differ from target backup")
    }
    create_backup(paths, state, &plan)
}

pub fn restore_backup(paths: &crate::model::Paths, backup: &Path) -> Result<()> {
    restore_core_files(paths, backup)?;
    let marketplaces_backup = backup.join("marketplaces.json");
    let plan_backup = backup.join("plan.json");
    let affected_marketplaces: BTreeSet<String> = if plan_backup.exists() {
        let plan: PendingPlan = read_json(&plan_backup)?;
        plan.changes
            .into_iter()
            .filter(|change| change.kind == "marketplace")
            .map(|change| change.target)
            .collect()
    } else {
        BTreeSet::new()
    };
    if marketplaces_backup.exists() {
        let marketplaces: std::collections::BTreeMap<String, PathBuf> =
            read_json(&marketplaces_backup)?;
        for (name, root) in &marketplaces {
            let snapshot = backup.join("marketplace-snapshots").join(name);
            if snapshot.is_dir()
                && (root.starts_with(&paths.codex_home) || root.starts_with(&paths.data_home))
            {
                copy_tree(&snapshot, root)?;
            }
        }
        let actual = marketplace_roots()?;
        for name in &affected_marketplaces {
            match marketplaces.get(name) {
                Some(expected_root) => {
                    let matches = actual.get(name).is_some_and(|actual_root| {
                        actual_root == expected_root
                            || (actual_root.exists()
                                && expected_root.exists()
                                && fs::canonicalize(actual_root).ok()
                                    == fs::canonicalize(expected_root).ok())
                    });
                    if !matches {
                        if actual.contains_key(name) {
                            remove_marketplace(name)?;
                        }
                        add_local_marketplace(expected_root)?;
                    }
                }
                None => {
                    if actual.contains_key(name) {
                        remove_marketplace(name)?;
                    }
                }
            }
        }
    }
    restore_core_files(paths, backup)?;
    let plugins_backup = backup.join("plugins.json");
    if plugins_backup.exists() {
        let plugins: Vec<InstalledPlugin> = read_json(&plugins_backup)?;
        restore_installed_plugins(&plugins)?;
    }
    restore_core_files(paths, backup)?;
    if marketplaces_backup.exists() {
        let expected: std::collections::BTreeMap<String, PathBuf> =
            read_json(&marketplaces_backup)?;
        let actual = marketplace_roots()?;
        for name in &affected_marketplaces {
            if expected.contains_key(name) != actual.contains_key(name) {
                anyhow::bail!(
                    "marketplace registration for {name} does not match the backup after restore"
                );
            }
        }
    }
    Ok(())
}

fn restore_core_files(paths: &crate::model::Paths, backup: &Path) -> Result<()> {
    for file in ["config.toml", "AGENTS.md"] {
        let source = backup.join(file);
        let destination = paths.codex_home.join(file);
        if backup.join(format!("{file}.absent")).exists() {
            if destination.exists() {
                fs::remove_file(&destination)?;
            }
        } else if source.exists() {
            atomic_write(&destination, &fs::read(&source)?)?;
        }
    }
    restore_agent_profiles(paths, backup)?;
    let state_backup = backup.join("state.toml");
    if state_backup.exists() {
        atomic_write(&paths.state_file, &fs::read(state_backup)?)?;
    }
    Ok(())
}

fn restore_agent_profiles(paths: &crate::model::Paths, backup: &Path) -> Result<()> {
    let profile_backup = backup.join("agent-profiles");
    if !profile_backup.is_dir() {
        return Ok(());
    }
    let previous: LocalState = read_toml(&backup.join("state.toml"))?;
    let plan: PendingPlan = read_json(&backup.join("plan.json"))?;
    let names: BTreeSet<_> = previous
        .managed_agent_profiles
        .iter()
        .chain(plan.managed_agent_profiles.iter())
        .cloned()
        .collect();
    let destination = paths.codex_home.join("agents");
    for name in names {
        let target = destination.join(format!("{name}.toml"));
        let source = profile_backup.join(format!("{name}.toml"));
        if profile_backup.join(format!("{name}.absent")).exists() {
            match fs::remove_file(&target) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    return Err(error).with_context(|| format!("remove {}", target.display()))
                }
            }
        } else if source.is_file() {
            atomic_write(&target, &fs::read(source)?)?;
        }
    }
    Ok(())
}
