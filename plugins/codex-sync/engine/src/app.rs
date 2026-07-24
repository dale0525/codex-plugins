use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use chrono::Utc;
use sha2::{Digest, Sha256};

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
use crate::reconcile::{
    add_local_marketplace, installed_plugins, marketplace_names, marketplace_roots, portable_name,
    reconcile_marketplaces, reconcile_plugins, remove_marketplace, restore_installed_plugins,
    validate_plugin_id, InstalledPlugin,
};
use crate::storage::{
    acquire_lock, atomic_write, copy_tree, ensure_data_dirs, load_state, read_json,
    read_optional_toml, read_toml, resolve_paths, save_state, tree_sha256, write_json,
};

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
        latest_backup: previous.and_then(|state| state.latest_backup),
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

    let desired_agents = fs::read(paths.repository_dir.join(&manifest.agents))
        .with_context(|| format!("read synchronized {}", manifest.agents))?;
    let current_agents = fs::read(paths.codex_home.join("AGENTS.md")).unwrap_or_default();
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
    let backup = create_backup(&paths, &state, &plan)?;
    let result = (|| -> Result<()> {
        apply_transaction(
            &paths,
            &manifest,
            &desired_profiles,
            &mut state,
            &plan,
            &rendered,
        )?;
        state.latest_backup = Some(backup.file_name().unwrap().to_string_lossy().into_owned());
        state.last_applied_commit = Some(plan.commit.clone());
        state.managed_paths = plan.managed_paths.clone();
        state.managed_agent_profiles = plan.managed_agent_profiles.clone();
        save_state(&paths, &state)
    })();
    if let Err(error) = result {
        return match restore_backup(&paths, &backup) {
            Ok(()) => Err(error).context("apply failed; restored the pre-apply backup"),
            Err(rollback_error) => {
                anyhow::bail!("apply failed: {error:#}; rollback also failed: {rollback_error:#}")
            }
        };
    }
    let _ = fs::remove_file(&paths.pending_plan);
    println!("Applied plan {} from commit {}", plan.id, plan.commit);
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
) -> Result<()> {
    atomic_write(
        &paths.codex_home.join("config.toml"),
        rendered_config.as_bytes(),
    )?;
    let agents = fs::read(paths.repository_dir.join(&manifest.agents))?;
    atomic_write(&paths.codex_home.join("AGENTS.md"), &agents)?;
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
    for message in reconcile_plugins(&plugins.plugins)? {
        println!("{message}");
    }
    state.last_applied_commit = Some(plan.commit.clone());
    Ok(())
}

fn create_backup(
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
    let plugin_state = installed_plugins().unwrap_or_default();
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

fn restore_backup(paths: &crate::model::Paths, backup: &Path) -> Result<()> {
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

pub fn rollback(backup_name: Option<&str>, approve: bool) -> Result<()> {
    if !approve {
        anyhow::bail!("rollback changes Codex configuration; rerun with --approve after review");
    }
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let state = load_state(&paths)?;
    let name = backup_name
        .map(str::to_owned)
        .or(state.latest_backup)
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
    restore_backup(&paths, &backup)?;
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
    let codex = std::env::var_os("CODEX_SYNC_CODEX_BIN").unwrap_or_else(|| "codex".into());
    let output = std::process::Command::new(codex)
        .arg("--version")
        .output()
        .context("Codex CLI is not available")?;
    if !output.status.success() {
        anyhow::bail!("Codex CLI version check failed");
    }
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
mod tests {
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
}
