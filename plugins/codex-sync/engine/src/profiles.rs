use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use crate::storage::atomic_write;

pub type AgentProfiles = BTreeMap<String, Vec<u8>>;

const REQUIRED_STRING_KEYS: &[&str] = &["name", "description", "developer_instructions"];

const SECRET_KEYS: &[&str] = &[
    "access_token",
    "api_key",
    "bearer_token",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
];

pub fn load_agent_profiles(repository: &Path, relative_directory: &str) -> Result<AgentProfiles> {
    let directory = repository.join(relative_directory);
    let metadata = fs::symlink_metadata(&directory).with_context(|| {
        format!(
            "read synchronized agent profiles at {}",
            directory.display()
        )
    })?;
    if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
        anyhow::bail!(
            "synchronized agent profiles path must be a directory: {}",
            directory.display()
        );
    }

    let mut profiles = AgentProfiles::new();
    for entry in fs::read_dir(&directory).with_context(|| {
        format!(
            "read synchronized agent profiles at {}",
            directory.display()
        )
    })? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let path = entry.path();
        if !file_type.is_file() || path.extension().and_then(|value| value.to_str()) != Some("toml")
        {
            anyhow::bail!(
                "agent profiles directory may contain only regular .toml files: {}",
                path.display()
            );
        }
        let name = path
            .file_stem()
            .and_then(|value| value.to_str())
            .context("agent profile filename is not valid UTF-8")?;
        validate_profile_name(name)?;
        let bytes = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
        let source = std::str::from_utf8(&bytes)
            .with_context(|| format!("agent profile is not UTF-8: {}", path.display()))?;
        let value: toml::Value = toml::from_str(source)
            .with_context(|| format!("parse agent profile TOML {}", path.display()))?;
        validate_profile(name, &value, &path)?;
        profiles.insert(name.to_owned(), bytes);
    }
    if profiles.is_empty() {
        anyhow::bail!(
            "synchronized agent profiles directory contains no .toml files: {}",
            directory.display()
        );
    }
    Ok(profiles)
}

pub fn current_profile_bytes(root: &Path, name: &str) -> Result<Option<Vec<u8>>> {
    validate_profile_name(name)?;
    let path = root.join(format!("{name}.toml"));
    match fs::symlink_metadata(&path) {
        Ok(metadata) => {
            if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
                anyhow::bail!(
                    "managed agent profile is not a regular file: {}",
                    path.display()
                );
            }
            Ok(Some(
                fs::read(&path).with_context(|| format!("read {}", path.display()))?,
            ))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error).with_context(|| format!("inspect {}", path.display())),
    }
}

pub fn profile_state_sha256(root: &Path, names: &BTreeSet<String>) -> Result<String> {
    if let Ok(metadata) = fs::symlink_metadata(root) {
        if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
            anyhow::bail!(
                "Codex agent profiles path must be a directory: {}",
                root.display()
            );
        }
    }
    let mut digest = Sha256::new();
    for name in names {
        digest.update(name.as_bytes());
        digest.update([0]);
        match current_profile_bytes(root, name)? {
            Some(bytes) => {
                digest.update(b"present");
                digest.update([0]);
                digest.update(bytes);
            }
            None => digest.update(b"absent"),
        }
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
}

pub fn managed_profile_names(
    desired: &AgentProfiles,
    previous: &[String],
) -> Result<BTreeSet<String>> {
    let mut names: BTreeSet<String> = desired.keys().cloned().collect();
    for name in previous {
        validate_profile_name(name)?;
        names.insert(name.clone());
    }
    Ok(names)
}

pub fn synchronize_agent_profiles(
    root: &Path,
    desired: &AgentProfiles,
    previous: &[String],
) -> Result<()> {
    match fs::symlink_metadata(root) {
        Ok(metadata) => {
            if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
                anyhow::bail!(
                    "Codex agent profiles path must be a directory: {}",
                    root.display()
                );
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir_all(root)
                .with_context(|| format!("create agent profiles directory {}", root.display()))?;
        }
        Err(error) => {
            return Err(error).with_context(|| format!("inspect {}", root.display()));
        }
    }
    for name in previous {
        validate_profile_name(name)?;
        if !desired.contains_key(name) {
            let path = root.join(format!("{name}.toml"));
            match fs::remove_file(&path) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    return Err(error).with_context(|| format!("remove {}", path.display()));
                }
            }
        }
    }
    for (name, bytes) in desired {
        atomic_write(&root.join(format!("{name}.toml")), bytes)?;
    }
    Ok(())
}

fn validate_profile(name: &str, value: &toml::Value, path: &Path) -> Result<()> {
    let table = value.as_table().with_context(|| {
        format!(
            "agent profile root must be a TOML table: {}",
            path.display()
        )
    })?;
    for key in REQUIRED_STRING_KEYS {
        let value = table
            .get(*key)
            .with_context(|| format!("agent profile is missing {key}: {}", path.display()))?;
        if value.as_str().is_none_or(str::is_empty) {
            anyhow::bail!(
                "agent profile {key} must be a non-empty string: {}",
                path.display()
            );
        }
    }
    if table.get("name").and_then(toml::Value::as_str) != Some(name) {
        anyhow::bail!(
            "agent profile name must match filename {name}.toml: {}",
            path.display()
        );
    }
    if table.contains_key("model_providers") {
        anyhow::bail!(
            "agent profiles must inherit complete provider definitions from providers.toml; remove model_providers from {}",
            path.display()
        );
    }
    validate_no_secrets(value, Vec::new(), path)
}

fn validate_no_secrets(value: &toml::Value, path: Vec<String>, source: &Path) -> Result<()> {
    match value {
        toml::Value::Table(table) => {
            for (key, child) in table {
                let mut child_path = path.clone();
                child_path.push(key.clone());
                let normalized = key.to_lowercase();
                if normalized != "env_key"
                    && !normalized.ends_with("_env")
                    && SECRET_KEYS
                        .iter()
                        .any(|candidate| normalized.contains(candidate))
                {
                    anyhow::bail!(
                        "refusing to synchronize probable secret at {} in {}",
                        child_path.join("."),
                        source.display()
                    );
                }
                validate_no_secrets(child, child_path, source)?;
            }
        }
        toml::Value::Array(values) => {
            for child in values {
                validate_no_secrets(child, path.clone(), source)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn validate_profile_name(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
    {
        anyhow::bail!(
            "agent profile name must use 1-64 letters, numbers, hyphens, or underscores: {value}"
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile(name: &str) -> String {
        format!(
            "name = \"{name}\"\ndescription = \"Test\"\nmodel = \"gpt-test\"\nmodel_reasoning_effort = \"medium\"\ndeveloper_instructions = \"Test instructions\"\n"
        )
    }

    #[test]
    fn loads_valid_profiles_and_rejects_name_mismatches() {
        let directory = tempfile::tempdir().unwrap();
        let profiles = directory.path().join("agents");
        fs::create_dir(&profiles).unwrap();
        fs::write(profiles.join("default.toml"), profile("default")).unwrap();
        assert_eq!(
            load_agent_profiles(directory.path(), "agents")
                .unwrap()
                .keys()
                .cloned()
                .collect::<Vec<_>>(),
            vec!["default"]
        );
        fs::write(profiles.join("default.toml"), profile("other")).unwrap();
        assert!(load_agent_profiles(directory.path(), "agents").is_err());
    }

    #[test]
    fn rejects_secret_fields() {
        let directory = tempfile::tempdir().unwrap();
        let profiles = directory.path().join("agents");
        fs::create_dir(&profiles).unwrap();
        fs::write(
            profiles.join("default.toml"),
            format!("{}\napi_key = \"secret\"\n", profile("default")),
        )
        .unwrap();
        assert!(load_agent_profiles(directory.path(), "agents").is_err());
    }

    #[test]
    fn rejects_profile_local_provider_definitions() {
        let directory = tempfile::tempdir().unwrap();
        let profiles = directory.path().join("agents");
        fs::create_dir(&profiles).unwrap();
        fs::write(
            profiles.join("default.toml"),
            format!(
                "{}\n[model_providers.cpa]\nname = \"Incomplete provider\"\n",
                profile("default")
            ),
        )
        .unwrap();
        assert!(load_agent_profiles(directory.path(), "agents").is_err());
    }

    #[test]
    fn digest_tracks_only_managed_names() {
        let directory = tempfile::tempdir().unwrap();
        fs::write(directory.path().join("default.toml"), "one").unwrap();
        fs::write(directory.path().join("personal.toml"), "first").unwrap();
        let names = BTreeSet::from(["default".to_owned()]);
        let before = profile_state_sha256(directory.path(), &names).unwrap();
        fs::write(directory.path().join("personal.toml"), "second").unwrap();
        assert_eq!(
            before,
            profile_state_sha256(directory.path(), &names).unwrap()
        );
        fs::write(directory.path().join("default.toml"), "two").unwrap();
        assert_ne!(
            before,
            profile_state_sha256(directory.path(), &names).unwrap()
        );
    }
}
