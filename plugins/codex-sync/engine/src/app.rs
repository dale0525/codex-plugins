use crate::agents::render_with_external_sections;
use crate::auth;
use crate::config::{
    changed_paths, classify_path, display_path, load_managed_values, read_current_config,
    render_config,
};
use crate::github::{http_client, GithubClient};
use crate::model::{
    LocalState, MarketplaceFile, MarketplaceSpec, PendingPlan, PlannedChange, PluginFile,
    RepositoryManifest, RepositoryRef, Risk, LOCAL_STATE_SCHEMA_VERSION,
};
use crate::profiles::{
    current_profile_bytes, load_agent_profiles, managed_profile_names, profile_state_sha256,
    synchronize_agent_profiles,
};
use crate::provision::{
    compensate_operations_recorded, create_operation_log, materialize_new_provisioners,
    migrate_receipts, new_operation_id, operation_log_path, operation_needs_recovery,
    prepare_rollback_runtime_recorded, provision_freshness, read_operation_log,
    restore_provisioners_recorded, retain_receipts, run_auto_provisioners_recorded,
    run_removal_provisioners_recorded, scan_operation_logs, validate_auto_provisioners,
    write_operation_log, ActionStatus, OperationLog, ProvisionFreshness, RuntimeOperation,
};
use crate::reconcile::{
    installed_plugins, marketplace_names, plugin_ids_to_remove, portable_name,
    reconcile_marketplaces, reconcile_plugins, validate_plugin_id, verify_codex_available,
};
use crate::storage::{
    acquire_lock, atomic_write, ensure_data_dirs, load_state, read_json, read_optional_toml,
    read_toml, resolve_paths, save_state, tree_sha256, write_json,
};
use crate::transaction::{
    create_backup, create_rollback_before_backup, restore_backup, OperationRecorder,
};
use anyhow::{Context, Result};
use chrono::Utc;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
const DEFAULT_GITHUB_CLIENT_ID: &str = "Iv23liN2J2Ryzkd99etp";
pub fn setup(
    repository: &str,
    device_id: &str,
    git_ref: &str,
    github_client_id: Option<String>,
    replace_existing: bool,
) -> Result<()> {
    validate_device_id(device_id)?;
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let previous = if paths.state_file.exists() {
        if !replace_existing {
            anyhow::bail!(
                "Codex Sync is already configured at {}; inspect status or rerun setup with --replace-existing after explicit review",
                paths.state_file.display()
            );
        }
        let previous = load_state(&paths)?;
        if previous.recovery_required {
            anyhow::bail!(
                "Codex Sync has a recovery-required operation; rollback or repair before setup"
            );
        }
        let backup_directory = paths.data_home.join("setup-backups");
        fs::create_dir_all(&backup_directory)?;
        let backup_name = format!("{}-state.toml", Utc::now().format("%Y%m%dT%H%M%S%.3fZ"));
        atomic_write(
            &backup_directory.join(backup_name),
            &fs::read(&paths.state_file)?,
        )?;
        Some(previous)
    } else {
        None
    };
    let state = LocalState {
        schema_version: LOCAL_STATE_SCHEMA_VERSION,
        repository: RepositoryRef::parse(repository, git_ref.to_owned())?,
        device_id: device_id.to_owned(),
        github_client_id: Some(
            github_client_id
                .filter(|value| !value.trim().is_empty())
                .or_else(environment_client_id)
                .or_else(|| {
                    previous
                        .as_ref()
                        .and_then(|state| state.github_client_id.clone())
                        .filter(|value| !value.trim().is_empty())
                })
                .unwrap_or_else(|| DEFAULT_GITHUB_CLIENT_ID.to_owned()),
        ),
        last_fetched_commit: None,
        fetched_repository_sha256: None,
        last_applied_commit: None,
        managed_paths: previous
            .as_ref()
            .map(|state| state.managed_paths.clone())
            .unwrap_or_default(),
        managed_agent_profiles: previous
            .as_ref()
            .map(|state| state.managed_agent_profiles.clone())
            .unwrap_or_default(),
        latest_backup: previous
            .as_ref()
            .and_then(|state| state.latest_backup.clone()),
        provision_receipts: previous
            .as_ref()
            .map(|state| state.provision_receipts.clone())
            .unwrap_or_default(),
        operation_log: None,
        recovery_required: false,
    };
    save_state(&paths, &state)?;
    println!(
        "Configured {} for device {}",
        state.repository.slug(),
        device_id
    );
    println!("Next: run `codex-sync login`, then `codex-sync sync`");
    Ok(())
}
pub fn login(client_id_override: Option<&str>, open_browser: bool) -> Result<()> {
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let mut state = load_state(&paths)?;
    let client_id = client_id_override
        .map(str::to_owned)
        .filter(|value| !value.trim().is_empty())
        .or_else(|| state.github_client_id.clone())
        .filter(|value| !value.trim().is_empty())
        .or_else(environment_client_id)
        .unwrap_or_else(|| DEFAULT_GITHUB_CLIENT_ID.to_owned());
    let client = http_client()?;
    auth::login(&client, &client_id, open_browser)?;
    state.github_client_id = Some(client_id);
    save_state(&paths, &state)?;
    println!("GitHub authentication succeeded and was stored in the OS credential store");
    Ok(())
}
fn environment_client_id() -> Option<String> {
    std::env::var("CODEX_SYNC_GITHUB_CLIENT_ID")
        .ok()
        .filter(|value| !value.trim().is_empty())
}
pub fn logout() -> Result<()> {
    auth::logout()?;
    println!("Removed the Codex Sync GitHub credential");
    Ok(())
}
pub fn sync(discard_local: bool) -> Result<PendingPlan> {
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let mut state = load_state(&paths)?;
    validate_state(&state)?;
    let client = http_client()?;
    let token = auth::resolve_token(&client, state.github_client_id.as_deref())?;
    let github = GithubClient::new(client, token)?;
    let commit = github.resolve_commit(&state.repository)?;
    let cached_digest = paths
        .repository_dir
        .is_dir()
        .then(|| tree_sha256(&paths.repository_dir))
        .transpose()?;
    let dirty = match (
        state.fetched_repository_sha256.as_deref(),
        cached_digest.as_deref(),
    ) {
        (Some(expected), Some(actual)) => expected != actual,
        (None, Some(_)) if state.last_fetched_commit.is_some() => true,
        _ => false,
    };
    if dirty && !discard_local {
        anyhow::bail!(
            "local repository cache has unpublished edits; publish them or rerun sync with --discard-local after explicit review"
        );
    }
    if discard_local
        || state.last_fetched_commit.as_deref() != Some(&commit)
        || !paths.repository_dir.exists()
    {
        github.download_repository(&state.repository, &commit, &paths.repository_dir)?;
        state.last_fetched_commit = Some(commit.clone());
        state.fetched_repository_sha256 = Some(tree_sha256(&paths.repository_dir)?);
        save_state(&paths, &state)?;
    } else if state.fetched_repository_sha256.is_none() {
        state.fetched_repository_sha256 = cached_digest;
        save_state(&paths, &state)?;
    }
    let plan = build_plan(&paths, &state, &commit)?;
    write_json(&paths.pending_plan, &plan)?;
    print_plan(&plan);
    Ok(plan)
}
fn build_plan(
    paths: &crate::model::Paths,
    state: &LocalState,
    commit: &str,
) -> Result<PendingPlan> {
    let manifest = load_repository_manifest(&paths.repository_dir)?;
    let desired = load_managed_values(&paths.repository_dir, &manifest, &state.device_id)?;
    let current_config = read_current_config(&paths.codex_home)?;
    let rendered = render_config(&current_config, &state.managed_paths, &desired)?;
    let mut changes = Vec::new();
    for path in changed_paths(&current_config, &rendered, &desired, &state.managed_paths) {
        let removing = !desired.contains_key(&path);
        changes.push(PlannedChange {
            risk: classify_path(&path),
            kind: "config".to_owned(),
            target: display_path(&path),
            summary: if removing {
                "remove previously synchronized value".to_owned()
            } else {
                "set synchronized value".to_owned()
            },
        });
    }
    let canonical_agents = fs::read(paths.repository_dir.join(&manifest.agents))
        .with_context(|| format!("read synchronized {}", manifest.agents))?;
    let current_agents = fs::read(paths.codex_home.join("AGENTS.md")).unwrap_or_default();
    let desired_agents = render_with_external_sections(
        &canonical_agents,
        &current_agents,
        &manifest.external_agents_sections,
    )?;
    if desired_agents != current_agents {
        changes.push(PlannedChange {
            risk: Risk::High,
            kind: "agents".to_owned(),
            target: "AGENTS.md".to_owned(),
            summary: "replace global agent instructions".to_owned(),
        });
    }
    let desired_profiles = load_agent_profiles(&paths.repository_dir, &manifest.agent_profiles)?;
    let profile_names = managed_profile_names(&desired_profiles, &state.managed_agent_profiles)?;
    for name in &profile_names {
        let current = current_profile_bytes(&paths.codex_home.join("agents"), name)?;
        let desired = desired_profiles.get(name);
        if current.as_ref() != desired {
            changes.push(PlannedChange {
                risk: Risk::High,
                kind: "agent-profile".to_owned(),
                target: format!("{name}.toml"),
                summary: match (current.is_some(), desired.is_some()) {
                    (false, true) => "add synchronized agent profile",
                    (true, true) => "replace synchronized agent profile",
                    (true, false) => "remove previously synchronized agent profile",
                    (false, false) => continue,
                }
                .to_owned(),
            });
        }
    }
    let marketplace_file: MarketplaceFile =
        read_optional_toml(&paths.repository_dir.join(&manifest.marketplaces))?;
    let configured_marketplaces = marketplace_names()?;
    for marketplace in &marketplace_file.marketplaces {
        changes.push(PlannedChange {
            risk: Risk::High,
            kind: "marketplace".to_owned(),
            target: marketplace.name().to_owned(),
            summary: if configured_marketplaces.contains(marketplace.name()) {
                "verify or refresh marketplace".to_owned()
            } else {
                "register marketplace".to_owned()
            },
        });
    }
    let plugin_file: PluginFile =
        read_optional_toml(&paths.repository_dir.join(&manifest.plugins))?;
    validate_desired_state(&marketplace_file, &plugin_file)?;
    let installed = installed_plugins()?;
    let managed_marketplaces: BTreeSet<_> = marketplace_file
        .marketplaces
        .iter()
        .map(|marketplace| marketplace.name().to_owned())
        .collect();
    for plugin_id in plugin_ids_to_remove(&installed, &plugin_file.plugins, &managed_marketplaces)?
    {
        changes.push(PlannedChange {
            risk: Risk::High,
            kind: "plugin".to_owned(),
            target: plugin_id,
            summary: "remove plugin no longer declared by synchronized state".to_owned(),
        });
    }
    for plugin in &plugin_file.plugins {
        let current = installed.iter().find(|value| value.plugin_id == plugin.id);
        let differs = if plugin.enabled {
            current.is_none_or(|value| !value.installed || !value.enabled)
        } else {
            current.is_some_and(|value| value.installed)
        };
        if differs {
            changes.push(PlannedChange {
                risk: Risk::High,
                kind: "plugin".to_owned(),
                target: plugin.id.clone(),
                summary: if plugin.enabled {
                    "install and enable plugin".to_owned()
                } else {
                    "remove plugin".to_owned()
                },
            });
        }
        if plugin.enabled
            && plugin.auto_provision
            && provision_freshness(plugin, state.provision_receipts.get(&plugin.id))?
                == ProvisionFreshness::NeedsRun
        {
            changes.push(PlannedChange {
                risk: Risk::High,
                kind: "plugin-provision".to_owned(),
                target: plugin.id.clone(),
                summary: "run the plugin's reviewed high-risk provisioner after installation"
                    .to_owned(),
            });
        }
    }
    let high_risk = changes.iter().any(|change| change.risk == Risk::High);
    let base_config_sha256 = sha256(current_config.as_bytes());
    let base_agents_sha256 = sha256(&current_agents);
    let base_agent_profiles_sha256 =
        profile_state_sha256(&paths.codex_home.join("agents"), &profile_names)?;
    let repository_sha256 = tree_sha256(&paths.repository_dir)?;
    let plan_seed = serde_json::to_vec(&(
        commit,
        &state.device_id,
        &base_config_sha256,
        &base_agents_sha256,
        &base_agent_profiles_sha256,
        &repository_sha256,
        &changes,
    ))?;
    let id = sha256(&plan_seed)[..16].to_owned();
    Ok(PendingPlan {
        id,
        generated_at: Utc::now().to_rfc3339(),
        commit: commit.to_owned(),
        device_id: state.device_id.clone(),
        base_config_sha256,
        base_agents_sha256,
        base_agent_profiles_sha256,
        repository_sha256,
        high_risk,
        changes,
        managed_paths: desired.keys().cloned().collect(),
        managed_agent_profiles: desired_profiles.keys().cloned().collect(),
    })
}
pub fn apply(plan_id: &str, approve_high_risk: bool) -> Result<()> {
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let mut state = load_state(&paths)?;
    ensure_recovery_clear(&paths, &state)?;
    let plan: PendingPlan =
        read_json(&paths.pending_plan).context("no pending plan; run `codex-sync sync` first")?;
    if plan.id != plan_id {
        anyhow::bail!("plan ID does not match the current pending plan");
    }
    if state.last_fetched_commit.as_deref() != Some(&plan.commit) {
        anyhow::bail!("repository snapshot changed after planning; run sync again");
    }
    if plan.high_risk && !approve_high_risk {
        anyhow::bail!(
            "plan contains high-risk changes; rerun with --approve-high-risk after review"
        );
    }
    let current_config = read_current_config(&paths.codex_home)?;
    let current_agents = fs::read(paths.codex_home.join("AGENTS.md")).unwrap_or_default();
    let manifest = load_repository_manifest(&paths.repository_dir)?;
    let desired_profiles = load_agent_profiles(&paths.repository_dir, &manifest.agent_profiles)?;
    let profile_names = managed_profile_names(&desired_profiles, &state.managed_agent_profiles)?;
    if sha256(current_config.as_bytes()) != plan.base_config_sha256
        || sha256(&current_agents) != plan.base_agents_sha256
        || profile_state_sha256(&paths.codex_home.join("agents"), &profile_names)?
            != plan.base_agent_profiles_sha256
    {
        anyhow::bail!("Codex configuration changed after planning; run sync again");
    }
    if tree_sha256(&paths.repository_dir)? != plan.repository_sha256 {
        anyhow::bail!("local repository cache changed after planning; run sync again");
    }
    let desired = load_managed_values(&paths.repository_dir, &manifest, &state.device_id)?;
    let rendered = render_config(&current_config, &state.managed_paths, &desired)?;
    let canonical_agents = fs::read(paths.repository_dir.join(&manifest.agents))?;
    let rendered_agents = render_with_external_sections(
        &canonical_agents,
        &current_agents,
        &manifest.external_agents_sections,
    )?;
    let desired_plugins: PluginFile =
        read_optional_toml(&paths.repository_dir.join(&manifest.plugins))?;
    validate_auto_provisioners(&desired_plugins.plugins, &state.provision_receipts)?;
    let backup = create_backup(&paths, &state, &plan)?;
    let operation_id = new_operation_id("apply", &plan.id);
    let operation_path = operation_log_path(&paths.data_home, &operation_id);
    let operation_log = OperationLog {
        schema_version: 5,
        operation_id: operation_id.clone(),
        kind: "apply".to_owned(),
        phase: "checkpointed".to_owned(),
        actions: Vec::new(),
        action_records: Vec::new(),
        backup: Some(backup.file_name().unwrap().to_string_lossy().into_owned()),
        recovery_required: false,
        before_backup: Some(
            backup
                .file_name()
                .context("apply backup has no basename")?
                .to_string_lossy()
                .into_owned(),
        ),
        target_state: Some(paths.state_file.to_string_lossy().into_owned()),
        before_state_digest: Some(sha256(toml::to_string(&state)?.as_bytes())),
        target_state_digest: None,
        supersedes: None,
        compensation_steps: Vec::new(),
    };
    create_operation_log(&operation_path, &operation_log)?;
    state.operation_log = Some(operation_path.to_string_lossy().into_owned());
    state.recovery_required = true;
    save_state(&paths, &state)?;
    let mut recorder = OperationRecorder::new(operation_path.clone(), operation_log)?;
    let mut provisioning_messages = Vec::new();
    let mut runtime_operations: Vec<RuntimeOperation> = Vec::new();
    let result = (|| -> Result<()> {
        recorder.set_phase("runtime_started")?;
        for message in run_removal_provisioners_recorded(
            &plan.changes,
            &desired_plugins.plugins,
            &state.provision_receipts,
            &mut runtime_operations,
            &mut recorder,
        )? {
            provisioning_messages.push(message);
        }
        recorder.set_phase("core_started")?;
        apply_transaction(
            &paths,
            &manifest,
            &desired_profiles,
            &mut state,
            &plan,
            &rendered,
            &rendered_agents,
        )?;
        state.latest_backup = Some(backup.file_name().unwrap().to_string_lossy().into_owned());
        state.last_applied_commit = Some(plan.commit.clone());
        state.managed_paths = plan.managed_paths.clone();
        state.managed_agent_profiles = plan.managed_agent_profiles.clone();
        let plugins: PluginFile =
            read_optional_toml(&paths.repository_dir.join(&manifest.plugins))?;
        let provision_targets = plan
            .changes
            .iter()
            .filter(|change| change.kind == "plugin-provision")
            .map(|change| change.target.clone())
            .collect::<BTreeSet<_>>();
        let (messages, receipts) = run_auto_provisioners_recorded(
            &plugins.plugins,
            &state.provision_receipts,
            &provision_targets,
            &mut runtime_operations,
            &paths.data_home,
            Some(&mut recorder),
        )?;
        provisioning_messages.extend(messages);
        state.provision_receipts = retain_receipts(receipts, &plugins.plugins);
        recorder.log.target_state_digest = Some(normalized_state_digest(&state)?);
        recorder.set_phase("commit-prepared")?;
        // Keep the recovery gate and log until the committed checkpoint is durable.
        save_state(&paths, &state)?;
        recorder.set_phase("committed")?;
        state = normalize_committed_state(&paths, &state, &recorder.log)?;
        Ok(())
    })();
    if let Err(error) = result {
        if recorder.log.phase == "committed" {
            // A failed convergence write is fail-closed and must not trigger rollback.
            recorder.log.recovery_required = true;
            recorder.persist()?;
            return Err(error).context(
                "apply committed checkpoint is durable but final state convergence failed; manual recovery required",
            );
        }
        if error.to_string().contains("durability is unknown") {
            recorder.log.phase = "recovery_required".to_owned();
            recorder.log.recovery_required = true;
            recorder.persist()?;
            return Err(error).context(
                "apply durability barrier failed after publication; target may be visible and manual recovery is required",
            );
        }
        if recorder.has_blocked_actions() || recorder.has_blocked_compensation() {
            recorder.log.phase = "manual-required".to_owned();
            recorder.log.recovery_required = true;
            recorder.persist()?;
            return Err(error).context(
                "apply provisioner outcome is manual-required; verify runtime before retrying",
            );
        }
        recorder.set_phase("compensating")?;
        // Freeze the complete reverse-runtime plan before restoring core.  No
        // compensation process may be spawned until this checkpoint is durable.
        recorder.materialize_compensation_plan(&runtime_operations)?;
        let backup_result = restore_backup(&paths, &backup);
        // Never spawn a reverse-runtime step when core restoration failed: the
        // runtime plan is only valid after the backup is known to be restored.
        let runtime_result = if backup_result.is_ok() {
            Some(compensate_operations_recorded(
                &runtime_operations,
                Some(&mut recorder),
            ))
        } else {
            None
        };
        if let Err(rollback_error) = backup_result {
            recorder.log.phase = "recovery_required".to_owned();
            recorder.log.recovery_required = true;
            if let Err(log_error) = recorder.persist() {
                anyhow::bail!("apply failed: {error:#}; recovery log failed: {log_error:#}; rollback also failed: {rollback_error:#}");
            }
            if let Some(Err(runtime_error)) = runtime_result {
                anyhow::bail!("apply failed: {error:#}; runtime compensation failed: {runtime_error:#}; rollback also failed: {rollback_error:#}");
            }
            anyhow::bail!("apply failed: {error:#}; rollback also failed: {rollback_error:#}");
        }
        if let Some(Err(runtime_error)) = runtime_result {
            recorder.log.phase = "recovery_required".to_owned();
            recorder.log.recovery_required = true;
            if let Err(log_error) = recorder.persist() {
                return Err(error).context(format!("runtime compensation failed: {runtime_error:#}; recovery log failed: {log_error:#}"));
            }
            let mut recovery_state = load_state(&paths)?;
            recovery_state.recovery_required = true;
            recovery_state.operation_log = Some(operation_path.to_string_lossy().into_owned());
            save_state(&paths, &recovery_state)?;
            return Err(error).context(format!("apply failed; core backup restored but runtime compensation failed: {runtime_error:#}"));
        }
        recorder.set_phase("reverted")?;
        return Err(error).context("apply failed; restored the pre-apply backup");
    }
    let _ = fs::remove_file(&paths.pending_plan);
    println!("Applied plan {} from commit {}", plan.id, plan.commit);
    for message in provisioning_messages {
        println!("{message}");
    }
    println!("Start a new Codex task so synchronized plugins and settings are reloaded");
    Ok(())
}
fn apply_transaction(
    paths: &crate::model::Paths,
    manifest: &RepositoryManifest,
    desired_profiles: &crate::profiles::AgentProfiles,
    state: &mut LocalState,
    plan: &PendingPlan,
    rendered_config: &str,
    rendered_agents: &[u8],
) -> Result<()> {
    atomic_write(
        &paths.codex_home.join("config.toml"),
        rendered_config.as_bytes(),
    )?;
    atomic_write(&paths.codex_home.join("AGENTS.md"), rendered_agents)?;
    synchronize_agent_profiles(
        &paths.codex_home.join("agents"),
        desired_profiles,
        &state.managed_agent_profiles,
    )?;
    let client = http_client()?;
    let token = auth::resolve_token(&client, state.github_client_id.as_deref())?;
    let github = GithubClient::new(client, token)?;
    let marketplaces: MarketplaceFile =
        read_optional_toml(&paths.repository_dir.join(&manifest.marketplaces))?;
    for message in
        reconcile_marketplaces(&marketplaces.marketplaces, &github, &paths.marketplaces_dir)?
    {
        println!("{message}");
    }
    let plugins: PluginFile = read_optional_toml(&paths.repository_dir.join(&manifest.plugins))?;
    materialize_new_provisioners(
        &plugins.plugins,
        &state.provision_receipts,
        &paths.data_home,
    )?;
    let managed_marketplaces: BTreeSet<_> = marketplaces
        .marketplaces
        .iter()
        .map(|marketplace| marketplace.name().to_owned())
        .collect();
    for message in reconcile_plugins(&plugins.plugins, &managed_marketplaces)? {
        println!("{message}");
    }
    state.last_applied_commit = Some(plan.commit.clone());
    Ok(())
}
pub fn rollback(backup_name: Option<&str>, approve: bool) -> Result<()> {
    if !approve {
        anyhow::bail!("rollback changes Codex configuration; rerun with --approve after review");
    }
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let mut state = load_state(&paths)?;
    ensure_recovery_clear(&paths, &state)?;
    state = load_state(&paths)?;
    let logged_backup = match state.operation_log.as_deref() {
        Some(path) => {
            let log = read_operation_log(Path::new(path))?;
            if log.kind == "rollback" && log.schema_version < 5 && operation_needs_recovery(&log) {
                anyhow::bail!(
                    "unfinished schema<5 rollback lacks a true before snapshot; manual recovery required"
                );
            }
            log.backup
        }
        None => None,
    };
    let name = backup_name
        .map(str::to_owned)
        .or(logged_backup)
        .or(state.latest_backup.clone())
        .context("no backup is available")?;
    if !name
        .chars()
        .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
    {
        anyhow::bail!("invalid backup name");
    }
    let backup = paths.backups_dir.join(&name);
    if !backup.is_dir() {
        anyhow::bail!("backup does not exist: {name}");
    }
    let mut target_state: LocalState = read_toml(&backup.join("state.toml"))?;
    migrate_receipts(&mut state.provision_receipts, &paths.data_home)?;
    migrate_receipts(&mut target_state.provision_receipts, &paths.data_home)?;
    let current_receipts = state.provision_receipts.clone();
    let interrupted_operation = state.operation_log.clone();
    let operation_id = new_operation_id("rollback", &name);
    let before_backup = create_rollback_before_backup(&paths, &state, &backup, &operation_id)?;
    let before_name = before_backup
        .file_name()
        .context("rollback before snapshot has no basename")?
        .to_string_lossy()
        .into_owned();
    if before_name == name {
        anyhow::bail!("rollback before snapshot must differ from target backup");
    }
    let operation_path = operation_log_path(&paths.data_home, &operation_id);
    let operation_log = OperationLog {
        schema_version: 5,
        operation_id: operation_id.clone(),
        kind: "rollback".to_owned(),
        phase: "checkpointed".to_owned(),
        actions: Vec::new(),
        action_records: Vec::new(),
        backup: Some(name.clone()),
        recovery_required: false,
        before_backup: Some(before_name),
        target_state: Some(backup.join("state.toml").to_string_lossy().into_owned()),
        before_state_digest: Some(sha256(toml::to_string(&state)?.as_bytes())),
        target_state_digest: Some(normalized_state_digest(&target_state)?),
        supersedes: state.operation_log.clone(),
        compensation_steps: Vec::new(),
    };
    create_operation_log(&operation_path, &operation_log)?;
    let mut recorder = OperationRecorder::new(operation_path.clone(), operation_log)?;
    let mut operations = Vec::new();
    if let Err(error) = restore_backup(&paths, &backup) {
        recorder.log.phase = "recovery_required".to_owned();
        recorder.log.recovery_required = true;
        recorder.persist()?;
        return Err(error);
    }
    // restore_backup restores the target snapshot, including its historical
    // state file. Re-assert the rollback recovery gate before any runtime
    // convergence so a crash cannot expose an ungated target state.
    state = target_state.clone();
    state.operation_log = Some(operation_path.to_string_lossy().into_owned());
    state.recovery_required = true;
    save_state(&paths, &state)?;
    recorder.set_phase("runtime_started")?;
    prepare_rollback_runtime_recorded(
        &current_receipts,
        &target_state.provision_receipts,
        &mut operations,
        Some(&mut recorder),
    )?;
    let target_runtime_receipts =
        target_runtime_receipts_for_restore(&current_receipts, &target_state.provision_receipts);
    if let Err(error) = restore_provisioners_recorded(
        &target_runtime_receipts,
        &mut operations,
        Some(&mut recorder),
    ) {
        recorder.log.phase = "recovery_required".to_owned();
        recorder.log.recovery_required = true;
        recorder.persist()?;
        if !recorder.has_blocked_actions() && !recorder.has_blocked_compensation() {
            let _ = compensate_operations_recorded(&operations, Some(&mut recorder));
        }
        let mut recovery_state = load_state(&paths)?;
        recovery_state.recovery_required = true;
        recovery_state.operation_log = Some(operation_path.to_string_lossy().into_owned());
        save_state(&paths, &recovery_state)?;
        anyhow::bail!("rollback restored configuration but runtime restoration failed: {error:#}");
    }
    recorder.set_phase("committed")?;
    if let Some(interrupted) = interrupted_operation.as_deref() {
        if interrupted != operation_path.to_string_lossy() {
            let mut old_log = read_operation_log(Path::new(interrupted))
                .context("read interrupted operation before superseding")?;
            old_log.phase = "superseded".to_owned();
            old_log.recovery_required = false;
            write_operation_log(Path::new(interrupted), &old_log)
                .context("durably supersede interrupted operation")?;
        }
    }
    state = target_state;
    state.operation_log = Some(operation_path.to_string_lossy().into_owned());
    state.recovery_required = true;
    normalize_committed_state(&paths, &state, &recorder.log)?;
    println!("Restored backup {name}; start a new Codex task");
    Ok(())
}
pub fn status(json: bool) -> Result<()> {
    let paths = resolve_paths()?;
    let state = load_state(&paths)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&state)?);
    } else {
        println!("Repository: {}", state.repository.slug());
        println!("Device: {}", state.device_id);
        println!(
            "Fetched: {}",
            state.last_fetched_commit.as_deref().unwrap_or("never")
        );
        println!(
            "Applied: {}",
            state.last_applied_commit.as_deref().unwrap_or("never")
        );
        println!("State: {}", paths.state_file.display());
        println!("Repository cache: {}", paths.repository_dir.display());
        println!("Backups: {}", paths.backups_dir.display());
        println!(
            "Managed agent profiles: {}",
            if state.managed_agent_profiles.is_empty() {
                "none".to_owned()
            } else {
                state.managed_agent_profiles.join(", ")
            }
        );
    }
    Ok(())
}
pub fn doctor() -> Result<()> {
    let paths = resolve_paths()?;
    ensure_data_dirs(&paths)?;
    let state = load_state(&paths)?;
    validate_state(&state)?;
    if paths.codex_home.join("AGENTS.override.md").exists() {
        anyhow::bail!("AGENTS.override.md shadows the synchronized global AGENTS.md");
    }
    if paths.repository_dir.exists() {
        let manifest = load_repository_manifest(&paths.repository_dir)?;
        let canonical_agents = fs::read(paths.repository_dir.join(&manifest.agents))?;
        let current_agents = fs::read(paths.codex_home.join("AGENTS.md")).unwrap_or_default();
        render_with_external_sections(
            &canonical_agents,
            &current_agents,
            &manifest.external_agents_sections,
        )?;
        load_managed_values(&paths.repository_dir, &manifest, &state.device_id)?;
        let desired_profiles =
            load_agent_profiles(&paths.repository_dir, &manifest.agent_profiles)?;
        let profile_names =
            managed_profile_names(&desired_profiles, &state.managed_agent_profiles)?;
        for name in profile_names {
            if current_profile_bytes(&paths.codex_home.join("agents"), &name)?.as_ref()
                != desired_profiles.get(&name)
            {
                println!("warning: synchronized agent profile drift: {name}.toml");
            }
        }
        let marketplaces: MarketplaceFile =
            read_optional_toml(&paths.repository_dir.join(&manifest.marketplaces))?;
        let plugins: PluginFile =
            read_optional_toml(&paths.repository_dir.join(&manifest.plugins))?;
        validate_desired_state(&marketplaces, &plugins)?;
        if state.fetched_repository_sha256.as_deref()
            != Some(tree_sha256(&paths.repository_dir)?.as_str())
        {
            println!("warning: local repository cache contains unpublished edits");
        }
    }
    let codex = verify_codex_available().context("Codex CLI is not available")?;
    println!("Codex CLI: {}", codex.display());
    println!("Codex Sync doctor found no blocking problems");
    Ok(())
}
pub fn publish(message: &str, approve: bool) -> Result<()> {
    if !approve {
        anyhow::bail!(
            "publishing changes the private GitHub repository; rerun with --approve after reviewing the repository cache"
        );
    }
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let mut state = load_state(&paths)?;
    validate_state(&state)?;
    let expected_base = state
        .last_fetched_commit
        .clone()
        .context("nothing has been fetched; run sync first")?;
    let manifest = load_repository_manifest(&paths.repository_dir)?;
    load_managed_values(&paths.repository_dir, &manifest, &state.device_id)?;
    scan_repository_for_obvious_secrets(&paths.repository_dir)?;
    let client = http_client()?;
    let token = auth::resolve_token(&client, state.github_client_id.as_deref())?;
    let github = GithubClient::new(client, token)?;
    let commit = github.publish_repository(
        &state.repository,
        &expected_base,
        &paths.repository_dir,
        message,
    )?;
    state.last_fetched_commit = Some(commit.clone());
    state.fetched_repository_sha256 = Some(tree_sha256(&paths.repository_dir)?);
    save_state(&paths, &state)?;
    println!("Published synchronized configuration as commit {commit}");
    println!("Run `codex-sync sync` to create a new local application plan");
    Ok(())
}
fn scan_repository_for_obvious_secrets(root: &Path) -> Result<()> {
    fn visit(root: &Path, directory: &Path) -> Result<()> {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let path = entry.path();
            if entry.file_type()?.is_dir() {
                if entry.file_name() != ".git" {
                    visit(root, &path)?;
                }
                continue;
            }
            if !entry.file_type()?.is_file() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_lowercase();
            if name == ".env"
                || name == "auth.json"
                || name.starts_with("id_rsa")
                || name.ends_with(".pem")
                || name.ends_with(".key")
            {
                anyhow::bail!(
                    "refusing to publish probable credential file {}",
                    path.strip_prefix(root).unwrap_or(&path).display()
                );
            }
            let bytes = fs::read(&path)?;
            if let Ok(text) = std::str::from_utf8(&bytes) {
                for marker in [
                    "-----BEGIN PRIVATE KEY-----",
                    "github_pat_",
                    "ghp_",
                    "gho_",
                    "ghu_",
                    "ghr_",
                ] {
                    if text.contains(marker) {
                        anyhow::bail!(
                            "refusing to publish probable secret marker in {}",
                            path.strip_prefix(root).unwrap_or(&path).display()
                        );
                    }
                }
            }
        }
        Ok(())
    }
    visit(root, root)
}
pub(crate) fn load_repository_manifest(repository: &Path) -> Result<RepositoryManifest> {
    let manifest: RepositoryManifest = read_toml(&repository.join("codex-sync.toml"))?;
    manifest.validate()?;
    Ok(manifest)
}
pub(crate) fn validate_desired_state(
    marketplaces: &MarketplaceFile,
    plugins: &PluginFile,
) -> Result<()> {
    let mut names = BTreeSet::new();
    for marketplace in &marketplaces.marketplaces {
        if !portable_name(marketplace.name()) {
            anyhow::bail!("invalid marketplace name: {}", marketplace.name());
        }
        if !names.insert(marketplace.name()) {
            anyhow::bail!("duplicate marketplace name: {}", marketplace.name());
        }
        match marketplace {
            MarketplaceSpec::Git {
                url,
                sparse,
                git_ref,
                ..
            } => {
                let parsed = reqwest::Url::parse(url)
                    .with_context(|| format!("invalid marketplace URL: {url}"))?;
                if parsed.scheme() != "https"
                    || !parsed.username().is_empty()
                    || parsed.password().is_some()
                {
                    anyhow::bail!(
                        "public Git marketplace URLs must use HTTPS without embedded credentials"
                    );
                }
                if git_ref.trim().is_empty() {
                    anyhow::bail!("marketplace Git ref cannot be empty");
                }
                for path in sparse {
                    let path = Path::new(path);
                    if path.is_absolute()
                        || path
                            .components()
                            .any(|part| matches!(part, std::path::Component::ParentDir))
                    {
                        anyhow::bail!("marketplace sparse path must stay inside the repository");
                    }
                }
            }
            MarketplaceSpec::GithubSnapshot {
                repository,
                git_ref,
                ..
            } => {
                RepositoryRef::parse(repository, git_ref.clone())?;
            }
        }
    }
    let mut plugin_ids = BTreeSet::new();
    for plugin in &plugins.plugins {
        validate_plugin_id(&plugin.id)?;
        if plugin.auto_provision && !plugin.enabled {
            anyhow::bail!(
                "plugin {} cannot enable auto_provision while disabled",
                plugin.id
            );
        }
        if !plugin_ids.insert(&plugin.id) {
            anyhow::bail!("duplicate plugin ID: {}", plugin.id);
        }
    }
    Ok(())
}
pub(crate) fn validate_state(state: &LocalState) -> Result<()> {
    if state.schema_version != LOCAL_STATE_SCHEMA_VERSION {
        anyhow::bail!("unsupported local state schema version");
    }
    validate_device_id(&state.device_id)
}

include!("app_recovery.rs");
fn validate_device_id(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
    {
        anyhow::bail!("device ID must use 1-64 letters, numbers, hyphens, or underscores");
    }
    Ok(())
}
fn sha256(value: &[u8]) -> String {
    hex::encode(Sha256::digest(value))
}
fn print_plan(plan: &PendingPlan) {
    println!("Plan {} for commit {}", plan.id, plan.commit);
    if plan.changes.is_empty() {
        println!("No changes");
        return;
    }
    for change in &plan.changes {
        println!(
            "- [{:?}] {} {}: {}",
            change.risk, change.kind, change.target, change.summary
        );
    }
    println!(
        "Apply with: codex-sync apply {}{}",
        plan.id,
        if plan.high_risk {
            " --approve-high-risk"
        } else {
            ""
        }
    );
}
#[cfg(test)]
mod tests;
