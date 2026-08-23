use anyhow::{Context, Result};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use crate::automations;
use crate::codex;
use crate::config::{self, ManagedValues};
use crate::migration;
use crate::model::{
    normalize_repository, validate_device, validate_git_ref, LocalState, Marketplace, Paths,
    STATE_SCHEMA_VERSION,
};
use crate::profiles;
use crate::storage::{
    self, acquire_lock, atomic_write, git_text, git_try, load_legacy_state, load_state,
    remove_if_exists, resolve_paths, run_git, save_state,
};
pub fn setup(repository: Option<&str>, device: Option<&str>, branch: &str) -> Result<()> {
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let previous = load_legacy_state(&paths)?;
    let (
        repository,
        device,
        branch,
        mut migration_pending,
        migration_pushed,
        managed_paths,
        managed_profiles,
        last_applied,
    ) = if paths.state_file.exists() {
        if let Ok(current) = storage::read_toml::<LocalState>(&paths.state_file) {
            let repo = repository
                .map(normalize_repository)
                .transpose()?
                .unwrap_or(current.repository);
            let dev = device.map(str::to_owned).unwrap_or(current.device);
            validate_device(&dev)?;
            let selected_branch = if branch == "main" && repository.is_none() {
                current.branch
            } else {
                branch.to_owned()
            };
            (
                repo,
                dev,
                selected_branch,
                current.migration_cleanup_pending,
                current.migration_pushed_commit,
                current.managed_paths,
                current.managed_profiles,
                current.last_applied_commit,
            )
        } else {
            let raw = previous.context("local state could not be read")?;
            let legacy_repo = raw
                .repository
                .as_ref()
                .context("legacy state repository is missing")?;
            let repo_text = legacy_repo
                .url
                .clone()
                .unwrap_or_else(|| format!("{}/{}", legacy_repo.owner, legacy_repo.name));
            let repo = normalize_repository(repository.unwrap_or(&repo_text))?;
            let dev = device
                .map(str::to_owned)
                .or(raw.device.clone())
                .or(raw.device_id.clone())
                .context("legacy state device is missing; pass --device")?;
            validate_device(&dev)?;
            let selected_branch = if branch == "main" {
                raw.branch
                    .clone()
                    .or(legacy_repo.branch.clone())
                    .unwrap_or_else(|| "main".to_owned())
            } else {
                branch.to_owned()
            };
            (
                repo,
                dev,
                selected_branch,
                true,
                None,
                raw.managed_paths,
                raw.managed_profiles,
                raw.last_applied_commit,
            )
        }
    } else {
        let repo = normalize_repository(repository.context("first setup requires --repository")?)?;
        let dev = device.context("first setup requires --device")?.to_owned();
        validate_device(&dev)?;
        (
            repo,
            dev,
            branch.to_owned(),
            false,
            None,
            Vec::new(),
            Vec::new(),
            None,
        )
    };
    validate_git_ref(&branch)?;
    let mut state = LocalState {
        schema_version: STATE_SCHEMA_VERSION,
        repository,
        branch,
        device,
        last_applied_commit: last_applied,
        managed_paths,
        managed_profiles,
        migration_cleanup_pending: migration_pending,
        migration_pushed_commit: migration_pushed.clone(),
        converged: false,
    };
    state.validate()?;
    let _fetched_commit = prepare_cache(&paths, &state)?;
    match repository_schema_version(&paths.cache)? {
        2 => {
            migration_pending = true;
            state.migration_cleanup_pending = true;
            state.migration_pushed_commit = None;
            state.converged = false;
            migration::migrate_repository(&paths.cache)?;
            validate_v3_repository(&paths.cache)?;
        }
        3 => {}
        schema => anyhow::bail!("unsupported repository schema version {schema}; expected 3"),
    }
    save_state(&paths, &state)?;
    println!(
        "Codex Sync bound to {} ({}) on device {}",
        state.repository, state.branch, state.device
    );
    if migration_pending {
        println!("legacy v2 state/repository detected; the next push will commit the v3 migration and current capture");
    }
    Ok(())
}
pub fn pull(dry_run: bool) -> Result<()> {
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let mut state = load_state(&paths)?;
    state.validate()?;
    let commit = match prepare_cache(&paths, &state) {
        Ok(commit) => commit,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    let schema = match repository_schema_version(&paths.cache) {
        Ok(schema) => schema,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    if schema == 2 {
        state.migration_cleanup_pending = true;
        state.migration_pushed_commit = None;
        state.converged = false;
        if !dry_run {
            save_state(&paths, &state)?;
        }
        println!("Remote repository schema v2 is pending migration; run push before pull can apply or clean up legacy state");
        return Ok(());
    }
    if let Err(error) = validate_v3_repository(&paths.cache) {
        return fail_pull(&paths, &mut state, dry_run, error);
    }
    let desired = match config::load_managed_values(&paths.cache, &state.device) {
        Ok(desired) => desired,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    let markets = match read_markets(&paths.cache) {
        Ok(markets) => markets,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    let plugins = match read_plugins(&paths.cache) {
        Ok(plugins) => plugins,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    let automations = match automations::read_repository(&paths.cache) {
        Ok(automations) => automations,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    let current = match config::read_current(&paths.codex_home) {
        Ok(current) => current,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    let rendered = match config::render_config(&current, &state.managed_paths, &desired) {
        Ok(rendered) => rendered,
        Err(error) => return fail_pull(&paths, &mut state, dry_run, error),
    };
    if dry_run {
        println!("dry-run pull from {commit}");
        if current != rendered {
            println!("- update config.toml managed leaves");
        }
        if read_optional_bytes(&paths.codex_home.join("AGENTS.md"))?
            != fs::read(paths.cache.join("AGENTS.md"))?
        {
            println!("- replace AGENTS.md");
        }
        let all_profiles = profiles::read_profiles(&paths.cache)?;
        let agents = fs::read(paths.cache.join("AGENTS.md"))?;
        let used_profiles = profiles::used_profile_names(&agents, &all_profiles)?;
        let desired_profiles = all_profiles
            .iter()
            .filter(|(name, _)| used_profiles.contains(name))
            .map(|(name, bytes)| (name.clone(), bytes.clone()))
            .collect::<profiles::Profiles>();
        let local_profiles = profiles::read_local_profiles(&paths.codex_home)?;
        for name in desired_profiles.keys() {
            match local_profiles.get(name) {
                None => println!("- add agent profile {name}.toml"),
                Some(current) if current != desired_profiles.get(name).expect("profile exists") => {
                    println!("- update agent profile {name}.toml")
                }
                _ => {}
            }
        }
        for name in local_profiles
            .keys()
            .filter(|name| !desired_profiles.contains_key(*name))
        {
            println!("- remove agent profile {name}.toml");
        }
        for action in automations::dry_run_actions(&paths.codex_home, &automations)? {
            println!("- {action}");
        }
        let report = codex::reconcile(&markets, &plugins, &paths.codex_home, true)?;
        for action in report.actions {
            println!("- {action}");
        }
        return Ok(());
    }
    let preflight = codex::reconcile(&markets, &plugins, &paths.codex_home, true);
    if let Err(error) = preflight {
        return fail_pull(
            &paths,
            &mut state,
            false,
            error.context("pull preflight failed"),
        );
    }
    let backup = match create_core_backup(&paths) {
        Ok(backup) => backup,
        Err(error) => {
            state.converged = false;
            save_state(&paths, &state)?;
            return Err(error.context("create pre-pull backup"));
        }
    };
    let result = apply_core(&paths, &state, &desired, &automations);
    if let Err(error) = result {
        if let Err(restore_error) = restore_core_backup(&paths, &backup) {
            state.converged = false;
            save_state(&paths, &state)?;
            return Err(error.context(format!(
                "core apply failed and backup restore failed: {restore_error:#}"
            )));
        }
        state.converged = false;
        save_state(&paths, &state)?;
        return Err(error);
    }
    if let Err(error) = codex::reconcile(&markets, &plugins, &paths.codex_home, false) {
        state.converged = false;
        save_state(&paths, &state)?;
        return Err(error.context(
            "plugin/marketplace convergence failed; core files were retained; rerun pull",
        ));
    }
    let cleanup_migration = state.migration_cleanup_pending
        && state
            .migration_pushed_commit
            .as_deref()
            .is_some_and(|pushed| is_commit_at_or_after(&paths.cache, pushed, &commit));
    if cleanup_migration {
        if let Err(error) = cleanup_legacy(&paths) {
            return fail_pull(
                &paths,
                &mut state,
                false,
                error.context("clean up legacy sync data"),
            );
        }
    }
    state.last_applied_commit = Some(commit.clone());
    state.managed_paths = desired.keys().cloned().collect();
    let remote_profiles = profiles::read_profiles(&paths.cache)?;
    state.managed_profiles =
        profiles::used_profile_names(&fs::read(paths.cache.join("AGENTS.md"))?, &remote_profiles)?;
    state.converged = true;
    if cleanup_migration {
        state.migration_cleanup_pending = false;
        state.migration_pushed_commit = None;
    }
    save_state(&paths, &state)?;
    println!("Pulled {commit}; Codex configuration and plugins converged");
    Ok(())
}
pub fn push(dry_run: bool, message: Option<&str>) -> Result<()> {
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let mut state = load_state(&paths)?;
    state.validate()?;
    let base_commit = prepare_cache(&paths, &state)?;
    if repository_schema_version(&paths.cache)? == 2 {
        state.migration_cleanup_pending = true;
        state.migration_pushed_commit = None;
        migration::migrate_repository(&paths.cache)?;
        state.converged = false;
    } else if state.migration_cleanup_pending {
        state.migration_cleanup_pending = true;
    }
    validate_v3_repository(&paths.cache)?;
    let capture = capture_current(&paths, &state)?;
    for warning in capture.warnings {
        println!("warning: {warning}");
    }
    scan_repository(&paths.cache)?;
    let changed = git_text(&["status", "--porcelain"], Some(&paths.cache))?;
    if changed.trim().is_empty() {
        if !dry_run {
            state.last_applied_commit = Some(base_commit.clone());
            state.managed_paths = capture.declared_paths;
            let local_profiles = profiles::read_local_profiles(&paths.codex_home)?;
            state.managed_profiles = profiles::used_profile_names(
                &fs::read(paths.codex_home.join("AGENTS.md")).unwrap_or_default(),
                &local_profiles,
            )?;
            state.converged = true;
            if state.migration_cleanup_pending && state.migration_pushed_commit.is_none() {
                state.migration_pushed_commit = Some(base_commit.clone());
            }
            save_state(&paths, &state)?;
        }
        println!("No configuration changes to push");
        return Ok(());
    }
    println!("push changes from {base_commit}:");
    for line in changed.lines() {
        println!("  {line}");
    }
    if dry_run {
        println!("dry-run: no commit or push performed");
        return Ok(());
    }
    let remote_commit = prepare_remote_only(&paths, &state)?;
    if remote_commit != base_commit {
        let _ = prepare_cache(&paths, &state);
        anyhow::bail!(
            "remote branch advanced from {base_commit} to {remote_commit}; rerun pull before push"
        );
    }
    run_git(&["add", "-A"], Some(&paths.cache))?;
    let commit_message = message
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| format!("Sync Codex configuration from {}", state.device));
    let output = storage::git_command()?
        .current_dir(&paths.cache)
        .args([
            "-c",
            "user.name=Logic Tan",
            "-c",
            "user.email=logictan89@gmail.com",
            "commit",
            "-m",
            &commit_message,
        ])
        .env("GIT_AUTHOR_NAME", "Logic Tan")
        .env("GIT_AUTHOR_EMAIL", "logictan89@gmail.com")
        .env("GIT_COMMITTER_NAME", "Logic Tan")
        .env("GIT_COMMITTER_EMAIL", "logictan89@gmail.com")
        .output()?;
    if !output.status.success() {
        state.converged = false;
        save_state(&paths, &state)?;
        anyhow::bail!(
            "git commit failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    let pushed = git_try(
        &[
            "push",
            "origin",
            &format!("HEAD:refs/heads/{}", state.branch),
        ],
        Some(&paths.cache),
    )?;
    if !pushed.status.success() {
        state.converged = false;
        save_state(&paths, &state)?;
        anyhow::bail!("remote race: git push rejected; rerun pull then push");
    }
    let new_commit = git_text(&["rev-parse", "HEAD"], Some(&paths.cache))?
        .trim()
        .to_owned();
    state.last_applied_commit = Some(new_commit.clone());
    if state.migration_cleanup_pending {
        state.migration_pushed_commit = Some(new_commit.clone());
    }
    state.managed_paths = capture.declared_paths;
    let local_profiles = profiles::read_local_profiles(&paths.codex_home)?;
    state.managed_profiles = profiles::used_profile_names(
        &fs::read(paths.codex_home.join("AGENTS.md")).unwrap_or_default(),
        &local_profiles,
    )?;
    state.converged = true;
    save_state(&paths, &state)?;
    println!("Pushed {new_commit}");
    Ok(())
}
pub fn status() -> Result<()> {
    let paths = resolve_paths()?;
    if !paths.state_file.exists() {
        println!("not configured");
        return Ok(());
    }
    let state = load_state(&paths)?;
    println!("repository: {}", state.repository);
    println!("branch: {}", state.branch);
    println!("device: {}", state.device);
    println!(
        "last applied commit: {}",
        state.last_applied_commit.as_deref().unwrap_or("never")
    );
    println!(
        "migration: {}",
        if state.migration_cleanup_pending {
            "pending"
        } else {
            "clean"
        }
    );
    println!(
        "convergence: {}",
        if state.converged {
            "converged"
        } else {
            "not converged"
        }
    );
    Ok(())
}
fn prepare_cache(paths: &Paths, state: &LocalState) -> Result<String> {
    if !is_git_repo(&paths.cache) {
        remove_if_exists(&paths.cache)?;
        if let Some(parent) = paths.cache.parent() {
            fs::create_dir_all(parent)?;
        }
        run_git(
            &[
                "clone",
                "--branch",
                &state.branch,
                &state.repository,
                paths.cache.to_str().context("cache path is not UTF-8")?,
            ],
            None,
        )?;
    }
    let commit = prepare_remote_only(paths, state)?;
    run_git(&["reset", "--hard", "FETCH_HEAD"], Some(&paths.cache))?;
    run_git(&["clean", "-fdx"], Some(&paths.cache))?;
    Ok(commit)
}

fn prepare_remote_only(paths: &Paths, state: &LocalState) -> Result<String> {
    run_git(
        &[
            "fetch",
            "origin",
            &format!("{}:refs/remotes/origin/{}", state.branch, state.branch),
        ],
        Some(&paths.cache),
    )?;
    let commit = git_text(&["rev-parse", "FETCH_HEAD"], Some(&paths.cache))?;
    Ok(commit.trim().to_owned())
}

fn is_git_repo(path: &Path) -> bool {
    path.join(".git").exists() || path.join("HEAD").exists() && path.join("config").exists()
}

fn validate_v3_repository(root: &Path) -> Result<()> {
    let value: toml::Value = storage::read_toml(&root.join("codex-sync.toml"))?;
    let table = value
        .as_table()
        .context("codex-sync.toml must be a table")?;
    if table.len() != 1
        || table
            .get("schema_version")
            .and_then(toml::Value::as_integer)
            != Some(3)
    {
        anyhow::bail!("repository must use fixed schema_version = 3");
    }
    if root.join("providers.toml").exists() {
        anyhow::bail!("providers.toml is not supported in repository schema v3; merge providers into config/common.toml");
    }
    let _ = config::load_managed_values(root, "__validation__").or_else(|error| {
        if error.to_string().contains("devices/__validation__") {
            Ok(BTreeMap::new())
        } else {
            Err(error)
        }
    })?;
    let _ = read_markets(root)?;
    let _ = read_plugins(root)?;
    let _ = automations::read_repository(root)?;
    Ok(())
}

fn repository_schema_version(root: &Path) -> Result<i64> {
    let value: toml::Value = storage::read_toml(&root.join("codex-sync.toml"))?;
    value
        .as_table()
        .and_then(|table| table.get("schema_version"))
        .and_then(toml::Value::as_integer)
        .context("codex-sync.toml schema_version is required")
}

fn is_commit_at_or_after(root: &Path, ancestor: &str, descendant: &str) -> bool {
    if ancestor == descendant {
        return true;
    }
    git_try(
        &["merge-base", "--is-ancestor", ancestor, descendant],
        Some(root),
    )
    .is_ok_and(|output| output.status.success())
}

fn read_markets(root: &Path) -> Result<Vec<Marketplace>> {
    let path = root.join("marketplaces.toml");
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error).with_context(|| format!("inspect {}", path.display())),
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("marketplaces.toml must not be a symlink")
        }
        Ok(_) => {}
    }
    let value: toml::Value = storage::read_toml(&path)?;
    let entries = value
        .as_table()
        .and_then(|table| table.get("marketplaces"))
        .context("marketplaces.toml requires a top-level marketplaces array")?
        .as_array()
        .context("marketplaces.toml marketplaces must be an array")?
        .clone();
    let mut result = Vec::new();
    for entry in entries {
        let table = entry
            .as_table()
            .context("marketplace entry must be a table")?;
        let name = table
            .get("name")
            .and_then(toml::Value::as_str)
            .context("marketplace name missing")?;
        if codex::is_protected_market(name) {
            continue;
        }
        let source = table
            .get("source")
            .and_then(toml::Value::as_str)
            .context("marketplace source must be the string git")?;
        if source != "git" {
            continue;
        }
        let market = Marketplace {
            name: name.to_owned(),
            url: table
                .get("url")
                .and_then(toml::Value::as_str)
                .context("marketplace URL missing")?
                .to_owned(),
            git_ref: match table.get("git_ref") {
                None => "main".to_owned(),
                Some(value) => value
                    .as_str()
                    .context("marketplace git_ref must be a string")?
                    .to_owned(),
            },
            sparse: match table.get("sparse") {
                None => Vec::new(),
                Some(value) => value
                    .as_array()
                    .context("marketplace sparse must be an array")?
                    .iter()
                    .map(|item| {
                        item.as_str()
                            .map(str::to_owned)
                            .context("marketplace sparse entries must be strings")
                    })
                    .collect::<Result<Vec<_>>>()?,
            },
        };
        market.validate()?;
        result.push(market);
    }
    Ok(result)
}

fn read_plugins(root: &Path) -> Result<BTreeSet<String>> {
    let path = root.join("plugins.toml");
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeSet::new()),
        Err(error) => return Err(error).with_context(|| format!("inspect {}", path.display())),
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("plugins.toml must not be a symlink")
        }
        Ok(_) => {}
    }
    let value: toml::Value = storage::read_toml(&path)?;
    let entries = value
        .as_table()
        .and_then(|table| table.get("plugins"))
        .context("plugins.toml requires a top-level plugins array")?
        .as_array()
        .context("plugins.toml plugins must be an array")?
        .clone();
    let mut result = BTreeSet::new();
    for entry in entries {
        let id = entry
            .as_str()
            .context("v3 plugins.toml must contain plugin ID strings")?;
        if id
            .split_once('@')
            .is_some_and(|(_, market)| codex::is_protected_market(market))
        {
            continue;
        }
        codex::validate_plugin_id(id)?;
        result.insert(id.to_owned());
    }
    Ok(result)
}

#[derive(Debug)]
struct CaptureResult {
    declared_paths: Vec<Vec<String>>,
    warnings: Vec<String>,
}

fn capture_current(paths: &Paths, state: &LocalState) -> Result<CaptureResult> {
    let current = config::read_current(&paths.codex_home)?;
    let common_path = paths.cache.join("config/common.toml");
    let device_path = paths
        .cache
        .join("devices")
        .join(format!("{}.toml", state.device));
    let common_value = config::read_optional_value(&common_path)?;
    let device_value = config::read_optional_value(&device_path)?;
    let common_paths = config::leaf_paths(&common_value);
    let device_paths = config::leaf_paths(&device_value);
    let mut declared = BTreeSet::new();
    declared.extend(common_paths.iter().cloned());
    declared.extend(device_paths.iter().cloned());
    for warning in config::unmanaged_paths(&current, &declared)? {
        println!(
            "unmanaged local key: {} (not captured)",
            config::display_path(&warning)
        );
    }
    let common_capture_paths = common_paths
        .iter()
        .filter(|path| !device_paths.contains(path))
        .cloned()
        .collect::<Vec<_>>();
    let common_kept = config::capture_declared(&current, &common_path, &common_capture_paths)?;
    let device_kept = config::capture_declared(&current, &device_path, &device_paths)?;
    let agents_path = paths.codex_home.join("AGENTS.md");
    let agents = match fs::read(&agents_path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(error) => return Err(error).with_context(|| format!("read {}", agents_path.display())),
    };
    atomic_write(&paths.cache.join("AGENTS.md"), &agents)?;
    let local_profiles = profiles::read_local_profiles(&paths.codex_home)?;
    let remote_profiles = profiles::read_profiles(&paths.cache)?;
    let used_profiles = profiles::used_profile_names(&agents, &local_profiles)?;
    for name in local_profiles
        .keys()
        .filter(|name| !used_profiles.contains(name))
    {
        println!("unused agent profile {name}.toml: omitted from push");
    }
    for name in remote_profiles
        .keys()
        .filter(|name| !used_profiles.contains(name))
    {
        remove_if_exists(&paths.cache.join("agents").join(format!("{name}.toml")))?;
    }
    for name in &used_profiles {
        let bytes = local_profiles.get(name).expect("used profile exists");
        atomic_write(
            &paths.cache.join("agents").join(format!("{name}.toml")),
            bytes,
        )?;
    }
    automations::capture_to_repository(&paths.cache, &paths.codex_home)?;
    let previous_markets = read_markets(&paths.cache)?;
    let inventory = codex::capture_inventory(&previous_markets)?;
    codex::write_markets(&paths.cache.join("marketplaces.toml"), &inventory.markets)?;
    codex::write_plugins(&paths.cache.join("plugins.toml"), &inventory.plugins)?;
    config::validate_values(&config::load_managed_values(&paths.cache, &state.device)?)?;
    let declared_paths = common_kept
        .into_iter()
        .chain(device_kept)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    Ok(CaptureResult {
        declared_paths,
        warnings: inventory.warnings,
    })
}

pub(crate) fn create_core_backup(paths: &Paths) -> Result<PathBuf> {
    remove_if_exists(&paths.backup)?;
    fs::create_dir_all(paths.backup.join("agents"))?;
    backup_file(
        &paths.codex_home.join("config.toml"),
        &paths.backup.join("config.toml"),
    )?;
    backup_file(
        &paths.codex_home.join("AGENTS.md"),
        &paths.backup.join("AGENTS.md"),
    )?;
    let local_profiles = profiles::read_local_profiles(&paths.codex_home)?;
    for name in local_profiles.keys() {
        backup_file(
            &paths.codex_home.join("agents").join(format!("{name}.toml")),
            &paths.backup.join("agents").join(format!("{name}.toml")),
        )?;
    }
    let desired_profiles = profiles::read_profiles(&paths.cache)?;
    for name in desired_profiles
        .keys()
        .filter(|name| !local_profiles.contains_key(*name))
    {
        backup_file(
            &paths.codex_home.join("agents").join(format!("{name}.toml")),
            &paths.backup.join("agents").join(format!("{name}.toml")),
        )?;
    }
    automations::create_backup(&paths.codex_home, &paths.cache, &paths.backup)?;
    Ok(paths.backup.clone())
}

fn backup_file(source: &Path, destination: &Path) -> Result<()> {
    match fs::symlink_metadata(source) {
        Ok(metadata) if metadata.file_type().is_file() => {
            atomic_write(
                destination,
                &fs::read(source).with_context(|| format!("read {}", source.display()))?,
            )?;
        }
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("refusing to back up symlink {}", source.display());
        }
        Ok(_) => anyhow::bail!("core backup source is not a file: {}", source.display()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let marker = destination.with_file_name(format!(
                "{}.absent",
                destination
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("file")
            ));
            atomic_write(&marker, b"")?;
        }
        Err(error) => return Err(error).with_context(|| format!("inspect {}", source.display())),
    }
    Ok(())
}

pub(crate) fn restore_core_backup(paths: &Paths, backup: &Path) -> Result<()> {
    for (source, destination) in [
        (
            backup.join("config.toml"),
            paths.codex_home.join("config.toml"),
        ),
        (backup.join("AGENTS.md"), paths.codex_home.join("AGENTS.md")),
    ] {
        restore_named(&source, &destination)?;
    }
    let agents_backup = backup.join("agents");
    match fs::symlink_metadata(&agents_backup) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!(
                "core backup agents path is a symlink: {}",
                agents_backup.display()
            )
        }
        Ok(metadata) if !metadata.file_type().is_dir() => {
            anyhow::bail!(
                "core backup agents path is not a directory: {}",
                agents_backup.display()
            )
        }
        Ok(_) => {
            for entry in fs::read_dir(&agents_backup)
                .with_context(|| format!("read {}", agents_backup.display()))?
            {
                let entry = entry?;
                let name = entry.file_name();
                let destination_name = name
                    .to_string_lossy()
                    .strip_suffix(".absent")
                    .map(str::to_owned)
                    .unwrap_or_else(|| name.to_string_lossy().into_owned());
                restore_file(
                    &entry.path(),
                    &paths.codex_home.join("agents").join(destination_name),
                )?;
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            anyhow::bail!(
                "core backup agents directory is missing: {}",
                agents_backup.display()
            )
        }
        Err(error) => {
            return Err(error).with_context(|| format!("inspect {}", agents_backup.display()))
        }
    }
    automations::restore_backup(&paths.codex_home, backup)?;
    Ok(())
}

fn restore_named(source: &Path, destination: &Path) -> Result<()> {
    match fs::symlink_metadata(source) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("core backup source is a symlink: {}", source.display())
        }
        Ok(_) => restore_file(source, destination),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let marker = source.with_file_name(format!(
                "{}.absent",
                source
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("file")
            ));
            match fs::symlink_metadata(&marker) {
                Ok(metadata) if metadata.file_type().is_symlink() => {
                    anyhow::bail!("core backup marker is a symlink: {}", marker.display())
                }
                Ok(metadata) if !metadata.file_type().is_file() => {
                    anyhow::bail!("core backup marker is not a file: {}", marker.display())
                }
                Ok(_) => remove_if_exists(destination),
                Err(marker_error) if marker_error.kind() == std::io::ErrorKind::NotFound => {
                    anyhow::bail!(
                        "core backup is incomplete: neither {} nor {} exists",
                        source.display(),
                        marker.display()
                    )
                }
                Err(marker_error) => {
                    Err(marker_error).with_context(|| format!("inspect {}", marker.display()))
                }
            }
        }
        Err(error) => Err(error).with_context(|| format!("inspect {}", source.display())),
    }
}

fn restore_file(source: &Path, destination: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(source)
        .with_context(|| format!("inspect backup file {}", source.display()))?;
    if metadata.file_type().is_symlink() {
        anyhow::bail!("core backup file is a symlink: {}", source.display());
    }
    if !metadata.file_type().is_file() {
        anyhow::bail!("core backup file is not a file: {}", source.display());
    }
    if source
        .file_name()
        .and_then(|value| value.to_str())
        .is_some_and(|name| name.ends_with(".absent"))
    {
        remove_if_exists(destination)
    } else {
        atomic_write(destination, &fs::read(source)?)
    }
}

fn apply_core(
    paths: &Paths,
    state: &LocalState,
    desired: &ManagedValues,
    desired_automations: &automations::Definitions,
) -> Result<()> {
    let current = config::read_current(&paths.codex_home)?;
    let rendered = config::render_config(&current, &state.managed_paths, desired)?;
    atomic_write(&paths.codex_home.join("config.toml"), rendered.as_bytes())?;
    atomic_write(
        &paths.codex_home.join("AGENTS.md"),
        &fs::read(paths.cache.join("AGENTS.md"))?,
    )?;
    profiles::mirror_profiles(&paths.cache, &paths.codex_home, &state.managed_profiles)?;
    automations::apply(&paths.cache, &paths.codex_home, desired_automations)?;
    Ok(())
}

fn scan_repository(root: &Path) -> Result<()> {
    for entry in walk_files(root)? {
        let relative = entry.strip_prefix(root).unwrap_or(&entry).to_string_lossy();
        if relative == ".git" || relative.starts_with(".git/") {
            continue;
        }
        let bytes = fs::read(&entry)?;
        let text = String::from_utf8_lossy(&bytes);
        if config::has_embedded_url_credentials(&text) {
            anyhow::bail!("refusing to push URL with embedded credentials in {relative}");
        }
        if entry.extension().and_then(|ext| ext.to_str()) == Some("toml") {
            let value: toml::Value =
                toml::from_str(&text).with_context(|| format!("parse {relative}"))?;
            scan_toml(&value, &[], &relative)?;
        }
    }
    Ok(())
}

fn scan_toml(value: &toml::Value, path: &[String], file: &str) -> Result<()> {
    match value {
        toml::Value::Table(table) => {
            for (key, value) in table {
                let mut next = path.to_vec();
                next.push(key.clone());
                scan_toml(value, &next, file)?;
            }
        }
        toml::Value::String(_value) => {
            let key = path
                .last()
                .map(String::as_str)
                .unwrap_or_default()
                .to_ascii_lowercase();
            let allowed = path.len() == 3
                && path[0] == "model_providers"
                && path[2] == "experimental_bearer_token";
            if !allowed
                && key != "env_key"
                && !key.ends_with("_env")
                && [
                    "token",
                    "secret",
                    "password",
                    "private_key",
                    "api_key",
                    "access_key",
                ]
                .iter()
                .any(|part| key.contains(part))
            {
                anyhow::bail!(
                    "refusing to push probable secret at {} in {file}",
                    path.join(".")
                );
            }
        }
        toml::Value::Array(values) => {
            for value in values {
                scan_toml(value, path, file)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn walk_files(root: &Path) -> Result<Vec<PathBuf>> {
    let mut result = Vec::new();
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        let path = entry.path();
        if path.file_name().is_some_and(|name| name == ".git") {
            continue;
        }
        if entry.file_type()?.is_dir() {
            result.extend(walk_files(&path)?);
        } else if entry.file_type()?.is_file() {
            result.push(path);
        }
    }
    Ok(result)
}

fn cleanup_legacy(paths: &Paths) -> Result<()> {
    for name in [
        "backups",
        "provision-artifacts",
        "provision-operations",
        "marketplaces",
        "pending-plan.json",
        "setup-backups",
    ] {
        remove_if_exists(&paths.data_home.join(name))?;
    }
    Ok(())
}

fn read_optional_bytes(path: &Path) -> Result<Vec<u8>> {
    match fs::read(path) {
        Ok(bytes) => Ok(bytes),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(error) => Err(error).with_context(|| format!("read {}", path.display())),
    }
}

fn fail_pull(
    paths: &Paths,
    state: &mut LocalState,
    dry_run: bool,
    error: anyhow::Error,
) -> Result<()> {
    if !dry_run {
        state.converged = false;
        save_state(paths, state)?;
    }
    Err(error)
}
