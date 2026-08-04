use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};

use crate::config::validate_values;
use crate::storage::atomic_write;

pub type Profiles = BTreeMap<String, Vec<u8>>;

pub fn read_profiles(repository: &Path) -> Result<Profiles> {
    read_profile_dir(&repository.join("agents"))
}

pub fn read_local_profiles(codex_home: &Path) -> Result<Profiles> {
    read_profile_dir(&codex_home.join("agents"))
}

fn read_profile_dir(directory: &Path) -> Result<Profiles> {
    let mut result = Profiles::new();
    match fs::symlink_metadata(directory) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(result),
        Err(error) => {
            return Err(error).with_context(|| format!("inspect {}", directory.display()))
        }
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("agent profiles path is a symlink: {}", directory.display())
        }
        Ok(metadata) if !metadata.file_type().is_dir() => {
            anyhow::bail!(
                "agent profiles path must be a directory: {}",
                directory.display()
            )
        }
        Ok(_) => {}
    }
    for entry in fs::read_dir(directory).with_context(|| format!("read {}", directory.display()))? {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_file()
            && path.extension().and_then(|value| value.to_str()) == Some("toml")
        {
            let name = path
                .file_stem()
                .and_then(|value| value.to_str())
                .context("profile name is not UTF-8")?;
            validate_profile_name(name)?;
            let bytes = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
            let text = std::str::from_utf8(&bytes)
                .with_context(|| format!("decode {}", path.display()))?;
            let value: toml::Value =
                toml::from_str(text).with_context(|| format!("parse {}", path.display()))?;
            validate_values(&crate::config::flatten(&value)?)?;
            result.insert(name.to_owned(), bytes);
        }
    }
    Ok(result)
}

pub fn mirror_profiles(
    repository: &Path,
    codex_home: &Path,
    _previous: &[String],
) -> Result<Vec<String>> {
    let desired = read_profiles(repository)?;
    let target = codex_home.join("agents");
    fs::create_dir_all(&target)?;
    for entry in fs::read_dir(&target)? {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_file()
            && path.extension().and_then(|value| value.to_str()) == Some("toml")
        {
            let name = path
                .file_stem()
                .and_then(|value| value.to_str())
                .unwrap_or_default();
            if !desired.contains_key(name) {
                fs::remove_file(&path).with_context(|| format!("remove {}", path.display()))?;
            }
        }
    }
    for (name, bytes) in &desired {
        atomic_write(&target.join(format!("{name}.toml")), bytes)?;
    }
    Ok(desired.keys().cloned().collect())
}

fn validate_profile_name(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_'))
    {
        anyhow::bail!("invalid agent profile name: {value}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_profiles_directory_is_empty() {
        let temporary = tempfile::tempdir().unwrap();
        assert!(read_profile_dir(&temporary.path().join("agents"))
            .unwrap()
            .is_empty());
    }

    #[test]
    fn non_directory_profiles_path_fails() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("agents");
        fs::write(&path, "not a directory").unwrap();
        assert!(read_profile_dir(&path).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn dangling_profiles_symlink_fails() {
        use std::os::unix::fs::symlink;
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("agents");
        symlink(temporary.path().join("missing"), &path).unwrap();
        assert!(read_profile_dir(&path).is_err());
    }
}
