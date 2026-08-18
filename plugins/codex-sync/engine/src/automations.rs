use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

use crate::storage::{atomic_write, remove_if_exists};

pub type Definitions = BTreeMap<String, Vec<u8>>;

const AUTOMATIONS_DIR: &str = "automations";
const DEFINITION_FILE: &str = "automation.toml";

/// Read the declaration-only automation files from a repository.
///
/// A repository is deliberately stricter than a local `$CODEX_HOME`: runtime
/// files such as `memory.md`, run logs, and SQLite databases must never enter
/// the sync domain.
pub fn read_repository(root: &Path) -> Result<Definitions> {
    let directory = root.join(AUTOMATIONS_DIR);
    let Some(directory) = checked_directory(&directory, false)? else {
        return Ok(Definitions::new());
    };
    let mut definitions = Definitions::new();
    let mut portable_ids = BTreeSet::new();
    for entry in
        fs::read_dir(&directory).with_context(|| format!("read {}", directory.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        let metadata =
            fs::symlink_metadata(&path).with_context(|| format!("inspect {}", path.display()))?;
        if metadata.file_type().is_symlink() {
            anyhow::bail!(
                "repository automation path must not be a symlink: {}",
                path.display()
            );
        }
        if !metadata.file_type().is_dir() {
            anyhow::bail!(
                "repository automations may contain only <id> directories: {}",
                path.display()
            );
        }
        let id = directory_name(&path)?;
        validate_id(&id)?;
        if !portable_ids.insert(id.to_ascii_lowercase()) {
            anyhow::bail!("automation ids must be unique ignoring ASCII case: {id}");
        }
        ensure_repository_definition_only(&path)?;
        let definition = read_definition(&path, &id)?;
        definitions.insert(id, definition);
    }
    Ok(definitions)
}

/// Read declaration files from the local automation store.  The local store
/// also contains lifecycle artifacts, so files at its root and directories
/// without `automation.toml` are ignored intentionally.
pub fn read_local(codex_home: &Path) -> Result<Definitions> {
    let directory = codex_home.join(AUTOMATIONS_DIR);
    let Some(directory) = checked_directory(&directory, true)? else {
        return Ok(Definitions::new());
    };
    let mut definitions = Definitions::new();
    let mut portable_ids = BTreeSet::new();
    for entry in
        fs::read_dir(&directory).with_context(|| format!("read {}", directory.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        let metadata =
            fs::symlink_metadata(&path).with_context(|| format!("inspect {}", path.display()))?;
        if metadata.file_type().is_symlink() {
            anyhow::bail!(
                "local automation path must not be a symlink: {}",
                path.display()
            );
        }
        if !metadata.file_type().is_dir() {
            // `.run-jitter-salt` and similar root files are runtime state.
            continue;
        }
        let id = directory_name(&path)?;
        let definition_path = path.join(DEFINITION_FILE);
        match fs::symlink_metadata(&definition_path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => {
                return Err(error).with_context(|| format!("inspect {}", definition_path.display()))
            }
            Ok(metadata) if metadata.file_type().is_symlink() => {
                anyhow::bail!(
                    "local automation definition must not be a symlink: {}",
                    definition_path.display()
                )
            }
            Ok(metadata) if !metadata.file_type().is_file() => {
                anyhow::bail!(
                    "local automation definition is not a file: {}",
                    definition_path.display()
                )
            }
            Ok(_) => {}
        }
        validate_id(&id)?;
        if !portable_ids.insert(id.to_ascii_lowercase()) {
            anyhow::bail!("automation ids must be unique ignoring ASCII case: {id}");
        }
        let definition = read_definition(&path, &id)?;
        definitions.insert(id, definition);
    }
    Ok(definitions)
}

/// Capture local declaration files into the repository, excluding all local
/// automation memory and other runtime artifacts.
pub fn capture_to_repository(repository: &Path, codex_home: &Path) -> Result<()> {
    let local = read_local(codex_home)?;
    let remote = read_repository(repository)?;
    for id in remote.keys().filter(|id| !local.contains_key(*id)) {
        remove_if_exists(&definition_path(repository, id)?)?;
    }
    for (id, bytes) in local {
        let bytes = match remote.get(&id) {
            Some(previous) => preserve_policy_metadata(previous, &bytes)?,
            None => bytes,
        };
        atomic_write(&definition_path(repository, &id)?, &bytes)?;
    }
    Ok(())
}

/// Apply repository declaration files while leaving local memory and runtime
/// files untouched.
pub fn apply(repository: &Path, codex_home: &Path, desired: &Definitions) -> Result<()> {
    // Validate the target before mutating anything.  This also prevents an
    // invalid definition supplied by a caller from being written locally.
    let repository_definitions = read_repository(repository)?;
    if &repository_definitions != desired {
        anyhow::bail!("repository automation definitions changed during pull");
    }
    let local = read_local(codex_home)?;
    for id in local.keys().filter(|id| !desired.contains_key(*id)) {
        remove_if_exists(&definition_path(codex_home, id)?)?;
    }
    for (id, bytes) in desired {
        atomic_write(&definition_path(codex_home, id)?, bytes)?;
    }
    Ok(())
}

pub fn dry_run_actions(codex_home: &Path, desired: &Definitions) -> Result<Vec<String>> {
    let local = read_local(codex_home)?;
    let mut actions = Vec::new();
    for (id, bytes) in desired {
        match local.get(id) {
            None => actions.push(format!("add automation {id}/automation.toml")),
            Some(current) if current != bytes => {
                actions.push(format!("update automation {id}/automation.toml"))
            }
            _ => {}
        }
    }
    for id in local.keys().filter(|id| !desired.contains_key(*id)) {
        actions.push(format!("remove automation {id}/automation.toml"));
    }
    Ok(actions)
}

/// Back up all definitions that can be touched by a pull.  Only
/// `automation.toml` is backed up; `memory.md` and other runtime state are not
/// part of the sync contract and are never restored.
pub fn create_backup(codex_home: &Path, repository: &Path, backup_root: &Path) -> Result<()> {
    fs::create_dir_all(backup_root.join(AUTOMATIONS_DIR))?;
    let local = read_local(codex_home)?;
    let desired = read_repository(repository)?;
    let ids = local
        .keys()
        .chain(desired.keys())
        .cloned()
        .collect::<BTreeSet<_>>();
    for id in ids {
        let source = definition_path(codex_home, &id)?;
        let destination = definition_path(backup_root, &id)?;
        backup_file(&source, &destination)?;
    }
    Ok(())
}

pub fn restore_backup(codex_home: &Path, backup_root: &Path) -> Result<()> {
    let backup_directory = backup_root.join(AUTOMATIONS_DIR);
    let Some(backup_directory) = checked_directory(&backup_directory, false)? else {
        anyhow::bail!(
            "automation backup directory is missing: {}",
            backup_directory.display()
        );
    };
    let current = read_local(codex_home)?;
    let mut restored = BTreeSet::new();
    for entry in fs::read_dir(&backup_directory)
        .with_context(|| format!("read {}", backup_directory.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        let metadata =
            fs::symlink_metadata(&path).with_context(|| format!("inspect {}", path.display()))?;
        if metadata.file_type().is_symlink() || !metadata.file_type().is_dir() {
            anyhow::bail!(
                "automation backup contains an invalid path: {}",
                path.display()
            );
        }
        let id = directory_name(&path)?;
        validate_id(&id)?;
        let source = path.join(DEFINITION_FILE);
        let marker = path.join(format!("{DEFINITION_FILE}.absent"));
        match fs::symlink_metadata(&source) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                anyhow::bail!(
                    "automation backup definition is a symlink: {}",
                    source.display()
                )
            }
            Ok(metadata) if !metadata.file_type().is_file() => {
                anyhow::bail!(
                    "automation backup definition is not a file: {}",
                    source.display()
                )
            }
            Ok(_) => {
                let bytes = fs::read(&source)
                    .with_context(|| format!("read backup {}", source.display()))?;
                validate_definition(&id, &bytes)?;
                atomic_write(&definition_path(codex_home, &id)?, &bytes)?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let marker_metadata = fs::symlink_metadata(&marker).with_context(|| {
                    format!("inspect automation backup marker {}", marker.display())
                })?;
                if marker_metadata.file_type().is_symlink()
                    || !marker_metadata.file_type().is_file()
                {
                    anyhow::bail!(
                        "automation backup marker is not a file: {}",
                        marker.display()
                    );
                }
                remove_if_exists(&definition_path(codex_home, &id)?)?;
            }
            Err(error) => {
                return Err(error).with_context(|| format!("inspect {}", source.display()))
            }
        }
        restored.insert(id);
    }
    for id in current.keys().filter(|id| !restored.contains(*id)) {
        remove_if_exists(&definition_path(codex_home, id)?)?;
    }
    Ok(())
}

pub fn definition_path(root: &Path, id: &str) -> Result<PathBuf> {
    validate_id(id)?;
    Ok(root.join(AUTOMATIONS_DIR).join(id).join(DEFINITION_FILE))
}

fn checked_directory(path: &Path, missing_ok: bool) -> Result<Option<PathBuf>> {
    match fs::symlink_metadata(path) {
        Err(error) if missing_ok && error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) if !missing_ok && error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error).with_context(|| format!("inspect {}", path.display())),
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!(
                "automations directory must not be a symlink: {}",
                path.display()
            )
        }
        Ok(metadata) if !metadata.file_type().is_dir() => {
            anyhow::bail!("automations path is not a directory: {}", path.display())
        }
        Ok(_) => Ok(Some(path.to_owned())),
    }
}

fn directory_name(path: &Path) -> Result<String> {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(str::to_owned)
        .context("automation directory name is not valid UTF-8")
}

fn read_definition(directory: &Path, id: &str) -> Result<Vec<u8>> {
    let path = directory.join(DEFINITION_FILE);
    let metadata =
        fs::symlink_metadata(&path).with_context(|| format!("inspect {}", path.display()))?;
    if metadata.file_type().is_symlink() {
        anyhow::bail!(
            "automation definition must not be a symlink: {}",
            path.display()
        );
    }
    if !metadata.file_type().is_file() {
        anyhow::bail!("automation definition is not a file: {}", path.display());
    }
    let bytes = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
    validate_definition(id, &bytes)?;
    Ok(bytes)
}

/// The desktop runner may rewrite a declaration without these optional sync
/// metadata keys. Preserve an explicitly configured value already in the
/// repository so a routine `push` cannot silently erase it.
fn preserve_policy_metadata(previous: &[u8], current: &[u8]) -> Result<Vec<u8>> {
    let previous_value: toml::Value =
        toml::from_str(std::str::from_utf8(previous).context("automation.toml must be UTF-8")?)
            .context("parse previous automation.toml")?;
    let mut current_value: toml::Value =
        toml::from_str(std::str::from_utf8(current).context("automation.toml must be UTF-8")?)
            .context("parse current automation.toml")?;
    let previous_table = previous_value
        .as_table()
        .context("previous automation.toml must contain a top-level table")?;
    let current_table = current_value
        .as_table_mut()
        .context("current automation.toml must contain a top-level table")?;
    let mut changed = false;
    for key in ["approval_policy", "sandbox_mode"] {
        if !current_table.contains_key(key) {
            if let Some(value) = previous_table.get(key) {
                current_table.insert(key.to_owned(), value.clone());
                changed = true;
            }
        }
    }
    if !changed {
        return Ok(current.to_vec());
    }
    toml::to_string(&current_value)
        .context("serialize automation.toml with preserved policy metadata")
        .map(String::into_bytes)
}

fn ensure_repository_definition_only(directory: &Path) -> Result<()> {
    for entry in fs::read_dir(directory).with_context(|| format!("read {}", directory.display()))? {
        let entry = entry?;
        let path = entry.path();
        let name = entry.file_name();
        if name != DEFINITION_FILE {
            anyhow::bail!(
                "repository automation directory may contain only automation.toml: {}",
                path.display()
            );
        }
        let metadata =
            fs::symlink_metadata(&path).with_context(|| format!("inspect {}", path.display()))?;
        if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
            anyhow::bail!(
                "repository automation definition is not a regular file: {}",
                path.display()
            );
        }
    }
    Ok(())
}

fn validate_definition(expected_id: &str, bytes: &[u8]) -> Result<()> {
    let text = std::str::from_utf8(bytes).context("automation.toml must be UTF-8")?;
    let value: toml::Value = toml::from_str(text).context("parse automation.toml")?;
    let table = value
        .as_table()
        .context("automation.toml must contain a top-level table")?;
    let allowed = [
        "version",
        "id",
        "kind",
        "name",
        "prompt",
        "status",
        "rrule",
        "model",
        "reasoning_effort",
        "notification_policy",
        "plugin_template_id",
        "execution_environment",
        "local_environment_config_path",
        "target",
        "cwds",
        "target_thread_id",
        "created_at",
        "updated_at",
        // These fields are accepted as explicit sync metadata.  The current
        // desktop automation serializer does not emit them; see the skill
        // documentation for the runtime support caveat.
        "approval_policy",
        "sandbox_mode",
    ];
    for key in table.keys() {
        if !allowed.contains(&key.as_str()) {
            anyhow::bail!("unsupported automation.toml field: {key}");
        }
    }
    let version = table
        .get("version")
        .and_then(toml::Value::as_integer)
        .context("automation.toml version must be integer 1")?;
    if version != 1 {
        anyhow::bail!("unsupported automation.toml version {version}; expected 1");
    }
    let id = required_string(table, "id")?;
    validate_id(id)?;
    if id != expected_id {
        anyhow::bail!("automation id {id} does not match directory {expected_id}");
    }
    let kind = required_string(table, "kind")?;
    if !matches!(kind, "cron" | "heartbeat") {
        anyhow::bail!("automation kind must be cron or heartbeat");
    }
    let status = required_string(table, "status")?;
    if !matches!(status, "ACTIVE" | "PAUSED" | "DELETED") {
        anyhow::bail!("automation status must be ACTIVE, PAUSED, or DELETED");
    }
    for key in ["name", "prompt", "rrule"] {
        let text = required_string(table, key)?;
        if text.chars().any(char::is_control) && key != "prompt" {
            anyhow::bail!("automation {key} must not contain control characters");
        }
        if key == "rrule" && text.is_empty() {
            anyhow::bail!("automation rrule must not be empty");
        }
    }
    if let Some(value) = table.get("model") {
        value
            .as_str()
            .context("automation model must be a string")?;
    }
    if let Some(value) = table.get("reasoning_effort") {
        let effort = value
            .as_str()
            .context("automation reasoning_effort must be a string")?;
        if !matches!(
            effort,
            "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra"
        ) {
            anyhow::bail!("unsupported automation reasoning_effort: {effort}");
        }
    }
    if let Some(value) = table.get("notification_policy") {
        if value.as_str() != Some("failed_runs_only") {
            anyhow::bail!("unsupported automation notification_policy");
        }
    }
    if let Some(value) = table.get("execution_environment") {
        let environment = value
            .as_str()
            .context("automation execution_environment must be a string")?;
        if !matches!(environment, "local" | "worktree") {
            anyhow::bail!("automation execution_environment must be local or worktree");
        }
    }
    if let Some(value) = table.get("local_environment_config_path") {
        let path = value
            .as_str()
            .context("automation local_environment_config_path must be a string")?;
        if path.chars().any(char::is_control) {
            anyhow::bail!("automation local_environment_config_path contains control characters");
        }
    }
    if let Some(value) = table.get("plugin_template_id") {
        value
            .as_str()
            .context("automation plugin_template_id must be a string")?;
    }
    if kind == "cron" {
        if let Some(value) = table.get("cwds") {
            let cwds = value
                .as_array()
                .context("automation cwds must be an array")?;
            for cwd in cwds {
                let cwd = cwd
                    .as_str()
                    .context("automation cwds entries must be strings")?;
                if cwd.chars().any(char::is_control) {
                    anyhow::bail!("automation cwd contains a control character");
                }
            }
        } else {
            anyhow::bail!("cron automation requires cwds");
        }
        if let Some(target) = table.get("target") {
            validate_target(target)?;
        }
        if table.contains_key("target_thread_id") {
            anyhow::bail!("cron automation cannot contain target_thread_id");
        }
    } else {
        let target_thread_id = required_string(table, "target_thread_id")?;
        if target_thread_id.is_empty() || target_thread_id.chars().any(char::is_control) {
            anyhow::bail!("heartbeat target_thread_id is invalid");
        }
        if table.contains_key("target") || table.contains_key("cwds") {
            anyhow::bail!("heartbeat automation cannot contain target or cwds");
        }
    }
    validate_timestamp(
        table
            .get("created_at")
            .context("automation created_at is required")?,
        "created_at",
    )?;
    validate_timestamp(
        table
            .get("updated_at")
            .context("automation updated_at is required")?,
        "updated_at",
    )?;
    if let Some(value) = table.get("approval_policy") {
        let policy = value
            .as_str()
            .context("automation approval_policy must be a string")?;
        if !matches!(policy, "untrusted" | "on-request" | "never" | "on-failure") {
            anyhow::bail!("unsupported automation approval_policy: {policy}");
        }
    }
    if let Some(value) = table.get("sandbox_mode") {
        let mode = value
            .as_str()
            .context("automation sandbox_mode must be a string")?;
        if !matches!(mode, "read-only" | "workspace-write" | "danger-full-access") {
            anyhow::bail!("unsupported automation sandbox_mode: {mode}");
        }
    }
    Ok(())
}

fn required_string<'a>(
    table: &'a toml::map::Map<String, toml::Value>,
    key: &str,
) -> Result<&'a str> {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .with_context(|| format!("automation {key} must be a string"))
}

fn validate_target(value: &toml::Value) -> Result<()> {
    let table = value
        .as_table()
        .context("automation target must be a table")?;
    let kind = table
        .get("type")
        .and_then(toml::Value::as_str)
        .context("automation target.type is required")?;
    match kind {
        "projectless" if table.len() == 1 => Ok(()),
        "project" => {
            let project_id = table
                .get("project_id")
                .and_then(toml::Value::as_str)
                .context("project automation target.project_id is required")?;
            if table.len() != 2 || project_id.is_empty() || project_id.chars().any(char::is_control)
            {
                anyhow::bail!("invalid project automation target");
            }
            Ok(())
        }
        _ => anyhow::bail!("automation target.type must be project or projectless"),
    }
}

fn validate_timestamp(value: &toml::Value, key: &str) -> Result<()> {
    let timestamp = value
        .as_integer()
        .with_context(|| format!("automation {key} must be an integer"))?;
    if !(0..=9_007_199_254_740_991).contains(&timestamp) {
        anyhow::bail!("automation {key} must be a non-negative JavaScript-safe integer");
    }
    Ok(())
}

fn validate_id(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || value == "."
        || value == ".."
        || value.starts_with('.')
        || value.ends_with('.')
        || !value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
        || is_windows_reserved_name(value)
    {
        anyhow::bail!(
            "automation id must use 1-128 ASCII letters, numbers, '.', '-', or '_': {value}"
        );
    }
    Ok(())
}

fn is_windows_reserved_name(value: &str) -> bool {
    let stem = value
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || stem
            .strip_prefix("COM")
            .or_else(|| stem.strip_prefix("LPT"))
            .is_some_and(|suffix| {
                matches!(suffix, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9")
            })
}

fn backup_file(source: &Path, destination: &Path) -> Result<()> {
    match fs::symlink_metadata(source) {
        Ok(metadata) if metadata.file_type().is_file() => {
            let bytes = fs::read(source).with_context(|| format!("read {}", source.display()))?;
            atomic_write(destination, &bytes)?;
        }
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("refusing to back up symlink {}", source.display());
        }
        Ok(_) => anyhow::bail!(
            "automation backup source is not a file: {}",
            source.display()
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let marker = destination.with_file_name(format!("{DEFINITION_FILE}.absent"));
            atomic_write(&marker, b"")?;
        }
        Err(error) => return Err(error).with_context(|| format!("inspect {}", source.display())),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid() -> Vec<u8> {
        br#"version = 1
id = "job"
kind = "cron"
name = "Job"
prompt = "Do work"
status = "ACTIVE"
rrule = "FREQ=DAILY"
execution_environment = "local"
target = { type = "projectless" }
cwds = ["~"]
created_at = 1
updated_at = 2
approval_policy = "never"
sandbox_mode = "danger-full-access"
"#
        .to_vec()
    }

    #[test]
    fn accepts_explicit_policy_metadata() {
        assert!(validate_definition("job", &valid()).is_ok());
    }

    #[test]
    fn preserves_policy_metadata_omitted_by_desktop_rewrite() {
        let previous = valid();
        let current = String::from_utf8(previous.clone())
            .unwrap()
            .replace("approval_policy = \"never\"\n", "")
            .replace("sandbox_mode = \"danger-full-access\"\n", "")
            .into_bytes();
        let preserved = preserve_policy_metadata(&previous, &current).unwrap();
        let text = String::from_utf8(preserved).unwrap();
        assert!(text.contains("approval_policy = \"never\""));
        assert!(text.contains("sandbox_mode = \"danger-full-access\""));
    }

    #[test]
    fn rejects_unsafe_ids() {
        assert!(validate_id("../job").is_err());
        assert!(validate_id("job/name").is_err());
    }
}
