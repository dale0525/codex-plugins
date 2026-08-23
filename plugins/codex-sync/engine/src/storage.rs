use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use anyhow::{Context, Result};
use fs2::FileExt;
use serde::de::DeserializeOwned;
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
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".codex")))
        .or_else(|| directories::BaseDirs::new().map(|dirs| dirs.home_dir().join(".codex")))
        .context("cannot resolve CODEX_HOME or HOME")?;
    let data_home = std::env::var_os("CODEX_SYNC_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| codex_home.join("codex-sync"));
    Ok(Paths {
        state_file: data_home.join("state.toml"),
        lock_file: data_home.join("sync.lock"),
        cache: data_home.join("git-cache"),
        backup: data_home.join("backup/previous"),
        data_home,
        codex_home,
    })
}

pub fn ensure_data_home(paths: &Paths) -> Result<()> {
    fs::create_dir_all(&paths.data_home)
        .with_context(|| format!("create {}", paths.data_home.display()))
}

pub fn acquire_lock(paths: &Paths) -> Result<ProcessLock> {
    ensure_data_home(paths)?;
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
    let state: LocalState =
        read_toml(&paths.state_file).context("Codex Sync is not configured; run setup first")?;
    Ok(state)
}

pub fn load_legacy_state(paths: &Paths) -> Result<Option<crate::model::LegacyState>> {
    if !paths.state_file.exists() {
        return Ok(None);
    }
    let text = fs::read_to_string(&paths.state_file)
        .with_context(|| format!("read {}", paths.state_file.display()))?;
    let value: toml::Value = toml::from_str(&text)
        .with_context(|| format!("parse local state {}", paths.state_file.display()))?;
    let is_legacy = value
        .as_table()
        .and_then(|table| table.get("repository"))
        .is_some_and(toml::Value::is_table);
    if !is_legacy {
        return Ok(Some(crate::model::LegacyState {
            schema_version: value
                .as_table()
                .and_then(|table| table.get("schema_version"))
                .and_then(toml::Value::as_integer)
                .unwrap_or(0) as u32,
            repository: None,
            device_id: None,
            device: None,
            branch: None,
            managed_paths: Vec::new(),
            managed_profiles: Vec::new(),
            last_applied_commit: None,
            migration_cleanup_pending: false,
        }));
    }
    Ok(Some(toml::from_str(&text).with_context(|| {
        format!("parse legacy local state {}", paths.state_file.display())
    })?))
}

pub fn save_state(paths: &Paths, state: &LocalState) -> Result<()> {
    state.validate()?;
    atomic_write(
        &paths.state_file,
        toml::to_string_pretty(state)
            .context("serialize local state")?
            .as_bytes(),
    )
}

pub fn read_toml<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let text = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    toml::from_str(&text).with_context(|| format!("parse TOML {}", path.display()))
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .with_context(|| format!("path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    let mut temp = NamedTempFile::new_in(parent)
        .with_context(|| format!("create temporary file in {}", parent.display()))?;
    temp.write_all(bytes).context("write temporary file")?;
    temp.flush().context("flush temporary file")?;
    temp.as_file().sync_all().context("sync temporary file")?;
    #[cfg(not(windows))]
    temp.persist(path)
        .map_err(|error| error.error)
        .with_context(|| format!("replace {}", path.display()))?;
    #[cfg(windows)]
    {
        let temporary = temp.into_temp_path();
        replace_on_windows(&temporary, path)?;
    }
    Ok(())
}

#[cfg(windows)]
fn replace_on_windows(source: &Path, destination: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };
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
    let moved = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if moved == 0 {
        return Err(std::io::Error::last_os_error())
            .with_context(|| format!("replace {}", destination.display()));
    }
    Ok(())
}

pub fn remove_if_exists(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {
            fs::remove_dir_all(path).with_context(|| format!("remove {}", path.display()))?
        }
        Ok(_) => fs::remove_file(path).with_context(|| format!("remove {}", path.display()))?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error).with_context(|| format!("inspect {}", path.display())),
    }
    Ok(())
}

pub fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    remove_if_exists(destination)?;
    fs::create_dir_all(destination).with_context(|| format!("create {}", destination.display()))?;
    copy_tree_contents(source, destination)
}

fn copy_tree_contents(source: &Path, destination: &Path) -> Result<()> {
    for entry in fs::read_dir(source).with_context(|| format!("read {}", source.display()))? {
        let entry = entry?;
        let src = entry.path();
        let dst = destination.join(entry.file_name());
        let ty = entry.file_type()?;
        if ty.is_dir() {
            fs::create_dir_all(&dst)?;
            copy_tree_contents(&src, &dst)?;
        } else if ty.is_file() {
            fs::create_dir_all(dst.parent().expect("file has parent"))?;
            fs::copy(&src, &dst)
                .with_context(|| format!("copy {} to {}", src.display(), dst.display()))?;
        } else {
            anyhow::bail!("refusing to copy special file {}", src.display());
        }
    }
    Ok(())
}

pub fn run_git(args: &[&str], cwd: Option<&Path>) -> Result<Output> {
    let mut command = git_command()?;
    command.args(args);
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    let output = command
        .output()
        .with_context(|| format!("run git {}", args.join(" ")))?;
    if !output.status.success() {
        anyhow::bail!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(output)
}

pub fn git_text(args: &[&str], cwd: Option<&Path>) -> Result<String> {
    let output = run_git(args, cwd)?;
    String::from_utf8(output.stdout)
        .with_context(|| format!("git {} output is not UTF-8", args.join(" ")))
}

pub fn git_try(args: &[&str], cwd: Option<&Path>) -> Result<Output> {
    let mut command = git_command()?;
    command.args(args);
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    command
        .output()
        .with_context(|| format!("run git {}", args.join(" ")))
}

/// Creates a Git command using the bootstrap-resolved executable when present.
///
/// Windows bootstrap sets `CODEX_SYNC_GIT_BIN` to either a discovered Git for
/// Windows installation or the verified private portable runtime. Keeping this
/// resolution here ensures every engine Git invocation, including commits, uses
/// the same executable instead of relying on a mutable process PATH.
pub fn git_command() -> Result<Command> {
    match std::env::var_os("CODEX_SYNC_GIT_BIN") {
        None => Ok(Command::new("git")),
        Some(value) => {
            let path = PathBuf::from(value);
            if !path.is_file() {
                anyhow::bail!(
                    "CODEX_SYNC_GIT_BIN does not point to a file: {}",
                    path.display()
                );
            }
            Ok(Command::new(path))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atomic_write_replaces_existing_file() {
        let temporary = tempfile::tempdir().unwrap();
        let target = temporary.path().join("nested/value.txt");
        atomic_write(&target, b"first").unwrap();
        atomic_write(&target, b"second").unwrap();
        assert_eq!(fs::read_to_string(target).unwrap(), "second");
    }
}
