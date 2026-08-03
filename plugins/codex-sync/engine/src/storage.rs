use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use fs2::FileExt;
use serde::de::DeserializeOwned;
use serde::Serialize;
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

use crate::model::{LocalState, Paths};

pub struct ProcessLock {
    file: fs::File,
}

impl Drop for ProcessLock {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

pub fn resolve_paths() -> Result<Paths> {
    let codex_home = std::env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .or_else(|| directories::BaseDirs::new().map(|dirs| dirs.home_dir().join(".codex")))
        .context("cannot resolve CODEX_HOME")?;
    let data_home = std::env::var_os("CODEX_SYNC_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| codex_home.join("codex-sync"));
    Ok(Paths {
        state_file: data_home.join("state.toml"),
        lock_file: data_home.join("sync.lock"),
        repository_dir: data_home.join("repository"),
        marketplaces_dir: data_home.join("marketplaces"),
        backups_dir: data_home.join("backups"),
        pending_plan: data_home.join("pending-plan.json"),
        data_home,
        codex_home,
    })
}

pub fn ensure_data_dirs(paths: &Paths) -> Result<()> {
    for path in [
        &paths.data_home,
        &paths.marketplaces_dir,
        &paths.backups_dir,
    ] {
        fs::create_dir_all(path).with_context(|| format!("create {}", path.display()))?;
    }
    Ok(())
}

pub fn acquire_lock(paths: &Paths) -> Result<ProcessLock> {
    ensure_data_dirs(paths)?;
    let file = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(&paths.lock_file)
        .with_context(|| format!("open {}", paths.lock_file.display()))?;
    file.try_lock_exclusive()
        .context("another Codex Sync process is already running")?;
    Ok(ProcessLock { file })
}

pub fn load_state(paths: &Paths) -> Result<LocalState> {
    read_toml(&paths.state_file).context("Codex Sync is not configured; run setup first")
}

pub fn save_state(paths: &Paths, state: &LocalState) -> Result<()> {
    let text = toml::to_string_pretty(state).context("serialize state")?;
    atomic_write(&paths.state_file, text.as_bytes())
}

pub fn read_toml<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let text = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    toml::from_str(&text).with_context(|| format!("parse TOML {}", path.display()))
}

pub fn read_optional_toml<T: DeserializeOwned + Default>(path: &Path) -> Result<T> {
    if path.exists() {
        read_toml(path)
    } else {
        Ok(T::default())
    }
}

pub fn read_json<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let bytes = fs::read(path).with_context(|| format!("read {}", path.display()))?;
    serde_json::from_slice(&bytes).with_context(|| format!("parse JSON {}", path.display()))
}

pub fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(value).context("serialize JSON")?;
    atomic_write(path, &bytes)
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .with_context(|| format!("path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    // Hold the directory handle across the rename.  This is required on
    // Windows (where a plain File::open(directory) is invalid) and ensures
    // the post-rename sync is performed on the same handle that was opened
    // before mutation.
    let directory = open_directory_for_sync(parent)?;
    let mut temporary = NamedTempFile::new_in(parent)
        .with_context(|| format!("create temporary file in {}", parent.display()))?;
    temporary.write_all(bytes).context("write temporary file")?;
    temporary.flush().context("flush temporary file")?;
    temporary
        .as_file()
        .sync_all()
        .context("sync temporary file")?;
    #[cfg(not(windows))]
    temporary
        .persist(path)
        .map_err(|error| error.error)
        .with_context(|| format!("replace {}", path.display()))?;
    #[cfg(windows)]
    {
        let temporary_path = temporary.into_temp_path();
        replace_file_on_windows(&temporary_path, path)?;
    }
    sync_open_directory(&directory, path)?;
    Ok(())
}

#[cfg(windows)]
fn replace_file_on_windows(source: &Path, destination: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let destination_display = destination.display().to_string();
    let source_wide = source
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let destination_wide = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let flags = MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH;
    let replaced = unsafe { MoveFileExW(source_wide.as_ptr(), destination_wide.as_ptr(), flags) };
    if replaced == 0 {
        return Err(std::io::Error::last_os_error())
            .with_context(|| format!("replace {destination_display}"));
    }
    Ok(())
}

/// Open a directory in a platform-correct way for durability barriers.
/// Windows requires FILE_FLAG_BACKUP_SEMANTICS for directory handles.
pub fn open_directory_for_sync(path: &Path) -> Result<fs::File> {
    #[cfg(unix)]
    {
        fs::File::open(path)
            .with_context(|| format!("open directory for durability: {}", path.display()))
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
        fs::OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
            .open(path)
            .with_context(|| format!("open directory for durability: {}", path.display()))
    }
}

pub fn sync_open_directory(directory: &fs::File, target: &Path) -> Result<()> {
    directory.sync_all().with_context(|| {
        format!(
            "target {} may be visible; durability is unknown after post-rename directory sync failure",
            target.display()
        )
    })
}

pub fn sync_directory_on_disk(path: &Path) -> Result<()> {
    let directory = open_directory_for_sync(path)?;
    directory
        .sync_all()
        .with_context(|| format!("sync directory for durability: {}", path.display()))
}

pub fn replace_tree_atomically(source: &Path, destination: &Path) -> Result<()> {
    let parent = destination
        .parent()
        .with_context(|| format!("path has no parent: {}", destination.display()))?;
    fs::create_dir_all(parent)?;
    let directory = open_directory_for_sync(parent)?;
    let backup_container = tempfile::tempdir_in(parent)?;
    let backup = backup_container.path().join("previous");
    let had_destination = destination.exists();
    if had_destination {
        fs::rename(destination, &backup).with_context(|| {
            format!(
                "move existing {} into transaction backup",
                destination.display()
            )
        })?;
    }
    if let Err(error) = fs::rename(source, destination) {
        if had_destination {
            let _ = fs::rename(&backup, destination);
        }
        return Err(error).with_context(|| format!("replace directory {}", destination.display()));
    }
    sync_open_directory(&directory, destination)?;
    Ok(())
}

pub fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    if destination.exists() {
        fs::remove_dir_all(destination)
            .with_context(|| format!("remove {}", destination.display()))?;
    }
    fs::create_dir_all(destination).with_context(|| format!("create {}", destination.display()))?;
    copy_tree_contents(source, destination)
}

fn copy_tree_contents(source: &Path, destination: &Path) -> Result<()> {
    for entry in fs::read_dir(source).with_context(|| format!("read {}", source.display()))? {
        let entry = entry?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        let file_type = entry.file_type()?;
        if file_type.is_dir() {
            fs::create_dir_all(&destination_path)?;
            copy_tree_contents(&source_path, &destination_path)?;
        } else if file_type.is_file() {
            fs::copy(&source_path, &destination_path).with_context(|| {
                format!(
                    "copy {} to {}",
                    source_path.display(),
                    destination_path.display()
                )
            })?;
        } else {
            anyhow::bail!("refusing to copy special file {}", source_path.display());
        }
    }
    Ok(())
}

pub fn tree_sha256(root: &Path) -> Result<String> {
    let mut files = Vec::new();
    collect_file_paths(root, root, &mut files)?;
    files.sort();
    let mut digest = Sha256::new();
    for path in files {
        let relative = path
            .strip_prefix(root)?
            .to_string_lossy()
            .replace('\\', "/");
        digest.update(relative.as_bytes());
        digest.update([0]);
        digest.update(fs::read(&path).with_context(|| format!("read {}", path.display()))?);
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
}

fn collect_file_paths(root: &Path, directory: &Path, output: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(directory).with_context(|| format!("read {}", directory.display()))? {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_dir() {
            collect_file_paths(root, &path, output)?;
        } else if entry.file_type()?.is_file() {
            let _ = path.strip_prefix(root)?;
            output.push(path);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atomic_write_replaces_content() {
        let directory = tempfile::tempdir().unwrap();
        let target = directory.path().join("nested/value.txt");
        atomic_write(&target, b"first").unwrap();
        atomic_write(&target, b"second").unwrap();
        assert_eq!(fs::read_to_string(target).unwrap(), "second");
    }

    #[cfg(windows)]
    #[test]
    fn windows_write_json_replaces_existing_pending_plan() {
        let directory = tempfile::tempdir().unwrap();
        let target = directory.path().join("pending-plan.json");
        fs::write(&target, br#"{"id":"stale"}"#).unwrap();
        write_json(&target, &serde_json::json!({ "id": "current" })).unwrap();
        assert_eq!(
            read_json::<serde_json::Value>(&target).unwrap(),
            serde_json::json!({ "id": "current" })
        );
    }

    #[test]
    fn directory_durability_helper_opens_and_syncs_directory() {
        let directory = tempfile::tempdir().unwrap();
        let handle = open_directory_for_sync(directory.path()).unwrap();
        sync_open_directory(&handle, &directory.path().join("published"))
            .expect("directory sync helper should be usable");
    }

    #[cfg(windows)]
    #[test]
    fn windows_directory_handle_uses_backup_semantics() {
        let directory = tempfile::tempdir().unwrap();
        let handle = open_directory_for_sync(directory.path()).unwrap();
        handle.sync_all().unwrap();
    }

    #[test]
    fn tree_digest_changes_with_content_not_creation_order() {
        let first = tempfile::tempdir().unwrap();
        let second = tempfile::tempdir().unwrap();
        fs::write(first.path().join("a"), "one").unwrap();
        fs::write(first.path().join("b"), "two").unwrap();
        fs::write(second.path().join("b"), "two").unwrap();
        fs::write(second.path().join("a"), "one").unwrap();
        assert_eq!(
            tree_sha256(first.path()).unwrap(),
            tree_sha256(second.path()).unwrap()
        );
        fs::write(second.path().join("a"), "changed").unwrap();
        assert_ne!(
            tree_sha256(first.path()).unwrap(),
            tree_sha256(second.path()).unwrap()
        );
    }

    #[test]
    fn save_state_failure_keeps_existing_target_untouched() {
        let directory = tempfile::tempdir().unwrap();
        let state_target = directory.path().join("state.toml");
        fs::create_dir_all(&state_target).unwrap();
        fs::write(state_target.join("sentinel"), b"checkpoint").unwrap();
        let paths = Paths {
            data_home: directory.path().to_owned(),
            state_file: state_target.clone(),
            lock_file: directory.path().join("lock"),
            repository_dir: directory.path().join("repository"),
            marketplaces_dir: directory.path().join("marketplaces"),
            backups_dir: directory.path().join("backups"),
            pending_plan: directory.path().join("pending-plan.json"),
            codex_home: directory.path().join("codex"),
        };
        let state = LocalState {
            schema_version: crate::model::LOCAL_STATE_SCHEMA_VERSION,
            repository: crate::model::RepositoryRef::parse("owner/repo", "main".to_owned())
                .unwrap(),
            device_id: "test".to_owned(),
            github_client_id: None,
            last_fetched_commit: None,
            fetched_repository_sha256: None,
            last_applied_commit: None,
            managed_paths: Vec::new(),
            managed_agent_profiles: Vec::new(),
            latest_backup: None,
            provision_receipts: std::collections::BTreeMap::new(),
            operation_log: None,
            recovery_required: false,
        };
        assert!(save_state(&paths, &state).is_err());
        assert_eq!(
            fs::read(state_target.join("sentinel")).unwrap(),
            b"checkpoint"
        );
    }
}
