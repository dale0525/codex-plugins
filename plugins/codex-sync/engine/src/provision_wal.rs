/// Read and validate a durable operation checkpoint before acting on it.
pub fn read_operation_log(path: &Path) -> Result<OperationLog> {
    let mut log: OperationLog = serde_json::from_slice(
        &fs::read(path).with_context(|| format!("read operation log {}", path.display()))?,
    )
    .with_context(|| format!("parse operation log {}", path.display()))?;
    if !matches!(log.schema_version, 1..=5) {
        anyhow::bail!("unsupported operation log schema version {}", log.schema_version);
    }
    if !matches!(
        log.phase.as_str(),
        "checkpointed" | "runtime_started" | "core_started" | "runtime_succeeded"
            | "commit-prepared" | "compensating" | "recovered" | "recovery_required"
            | "superseded" | "committed" | "reverted" | "manual-required"
    ) {
        anyhow::bail!("unknown operation log phase: {}", log.phase);
    }
    if log.schema_version >= 2 {
        let schema_version = log.schema_version;
        let operation_id = log.operation_id.clone();
        let mut action_ids = std::collections::BTreeSet::new();
        for (index, action) in log.action_records.iter_mut().enumerate() {
            if action.plugin_id.is_empty() || action.receipt.plugin_id != action.plugin_id {
                anyhow::bail!("operation action has invalid plugin receipt");
            }
            if schema_version >= 3 && action.action_id.is_empty() {
                anyhow::bail!("schema-3 operation action has no stable action ID");
            }
            if schema_version >= 3 && !action_ids.insert(action.action_id.clone()) {
                anyhow::bail!("schema-3 operation log contains duplicate action ID {}", action.action_id);
            }
            if schema_version == 2 && action.action_id.is_empty() {
                action.action_id = format!("{}-legacy-a{}", operation_id, index + 1);
                action.operation_kind = if action.uninstall { "uninstall" } else { "setup" }.to_owned();
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
            }
        }
    }
    Ok(log)
}

pub fn operation_needs_recovery(log: &OperationLog) -> bool {
    !matches!(log.phase.as_str(), "committed" | "reverted" | "superseded") || log.recovery_required
}

pub fn new_operation_id(kind: &str, hint: &str) -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let sequence = COUNTER.fetch_add(1, Ordering::Relaxed);
    let seed = format!(
        "{kind}:{hint}:{}:{}",
        Utc::now().timestamp_nanos_opt().unwrap_or_default(),
        std::process::id() ^ sequence as u32
    );
    hex::encode(Sha256::digest(seed.as_bytes()))[..24].to_owned()
}

pub fn unfinished_operation_logs(data_home: &Path) -> Result<Vec<(PathBuf, OperationLog)>> {
    let directory = data_home.join("provision-operations");
    if !directory.is_dir() {
        return Ok(Vec::new());
    }
    let mut paths = fs::read_dir(&directory)?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| path.extension().is_some_and(|extension| extension == "json"))
        .collect::<Vec<_>>();
    paths.sort();
    let mut unfinished = Vec::new();
    for path in paths {
        let log = read_operation_log(&path)?;
        if operation_needs_recovery(&log) {
            unfinished.push((path, log));
        }
    }
    Ok(unfinished)
}

/// Read every operation checkpoint without normalizing or writing anything.
/// Recovery callers use this as a preflight barrier before mutating state,
/// logs, core files, or runtime processes.
pub fn scan_operation_logs(data_home: &Path) -> Result<Vec<(PathBuf, OperationLog)>> {
    unfinished_operation_logs(data_home)
}

#[allow(dead_code)]
pub fn normalize_running_actions(path: &Path, input: &OperationLog) -> Result<OperationLog> {
    let mut log = input.clone();
    let mut changed = false;
    for action in &mut log.action_records {
        if action.status == ActionStatus::Running {
            action.status = ActionStatus::ManualRequired;
            action.phase = "manual-required".to_owned();
            action.message = Some("action outcome is ambiguous after restart; verify runtime manually before rollback".to_owned());
            changed = true;
        }
    }
    if changed {
        log.phase = "manual-required".to_owned();
        log.recovery_required = true;
        write_operation_log(path, &log)?;
    }
    Ok(log)
}

pub fn operation_log_path(data_home: &Path, operation_id: &str) -> PathBuf {
    data_home.join("provision-operations").join(format!("{operation_id}.json"))
}

pub fn write_operation_log(path: &Path, log: &OperationLog) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let bytes = serde_json::to_vec_pretty(log)?;
    let parent = path.parent().unwrap_or(Path::new("."));
    let directory = crate::storage::open_directory_for_sync(parent)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    use std::io::Write;
    temporary.write_all(&bytes)?;
    temporary.flush()?;
    temporary.as_file().sync_all()?;
    temporary.persist(path).map_err(|error| error.error)?;
    crate::storage::sync_open_directory(&directory, path)?;
    Ok(())
}

pub fn create_operation_log(path: &Path, log: &OperationLog) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let bytes = serde_json::to_vec_pretty(log)?;
    let parent = path.parent().unwrap_or(Path::new("."));
    let directory = crate::storage::open_directory_for_sync(parent)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    use std::io::Write;
    temporary.write_all(&bytes)?;
    temporary.flush()?;
    temporary.as_file().sync_all()?;
    temporary
        .persist_noclobber(path)
        .map_err(|error| error.error)?;
    crate::storage::sync_open_directory(&directory, path)?;
    Ok(())
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "kebab-case")]
enum WindowsShell {
    #[default]
    Pwsh,
    WindowsPowershell,
}

fn resolve_windows_powershell_from(root: Option<&std::ffi::OsStr>) -> String {
    if let Some(root) = root {
        let candidate = PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe");
        if candidate.is_file() {
            return candidate.to_string_lossy().into_owned();
        }
    }
    "powershell.exe".to_owned()
}

fn resolve_windows_powershell() -> Result<String> {
    Ok(resolve_windows_powershell_from(std::env::var_os("SystemRoot").as_deref()))
}

fn launcher_for(windows: bool, shell: WindowsShell) -> Result<(String, Vec<String>)> {
    if !windows {
        return Ok(("/bin/sh".to_owned(), Vec::new()));
    }
    let args = vec![
        "-NoProfile".to_owned(),
        "-NonInteractive".to_owned(),
        "-ExecutionPolicy".to_owned(),
        "Bypass".to_owned(),
        "-File".to_owned(),
    ];
    let launcher = match shell {
        WindowsShell::Pwsh => "pwsh".to_owned(),
        WindowsShell::WindowsPowershell => resolve_windows_powershell()?,
    };
    Ok((launcher, args))
}

fn safe_child(root: &Path, relative: &str) -> Result<PathBuf> {
    let relative = Path::new(relative);
    if relative.is_absolute()
        || relative
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        anyhow::bail!("plugin provisioning path must stay inside the marketplace")
    }
    let root = fs::canonicalize(root)
        .with_context(|| format!("resolve marketplace root {}", root.display()))?;
    let child = fs::canonicalize(root.join(relative))
        .with_context(|| format!("resolve plugin provisioning path {relative:?}"))?;
    if child != root && !child.starts_with(&root) {
        anyhow::bail!("plugin provisioning path escapes the marketplace")
    }
    Ok(child)
}
