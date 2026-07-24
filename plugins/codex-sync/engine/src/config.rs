use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use toml_edit::{DocumentMut, Item};

use crate::model::{ProviderFile, RepositoryManifest, Risk};
use crate::storage::{read_optional_toml, read_toml};

pub type ManagedValues = BTreeMap<Vec<String>, toml::Value>;

pub fn load_managed_values(
    repository: &Path,
    manifest: &RepositoryManifest,
    device_id: &str,
) -> Result<ManagedValues> {
    let mut values = ManagedValues::new();
    let common_path = repository.join(&manifest.common_config);
    if common_path.exists() {
        let common: toml::Value = read_toml(&common_path)?;
        flatten_table(&mut values, Vec::new(), common)?;
    }
    let device_path = repository
        .join(&manifest.devices)
        .join(format!("{device_id}.toml"));
    if device_path.exists() {
        let device: toml::Value = read_toml(&device_path)?;
        flatten_table(&mut values, Vec::new(), device)?;
    }
    let providers: ProviderFile = read_optional_toml(&repository.join(&manifest.providers))?;
    for (name, provider) in providers.providers {
        validate_segment(&name)?;
        flatten_table(
            &mut values,
            vec!["model_providers".to_owned(), name],
            provider,
        )?;
    }
    validate_no_secrets(&values)?;
    Ok(values)
}

fn flatten_table(
    output: &mut ManagedValues,
    prefix: Vec<String>,
    value: toml::Value,
) -> Result<()> {
    match value {
        toml::Value::Table(table) => {
            for (key, value) in table {
                validate_segment(&key)?;
                let mut path = prefix.clone();
                path.push(key);
                flatten_table(output, path, value)?;
            }
        }
        value => {
            if prefix.is_empty() {
                anyhow::bail!("configuration root must be a TOML table");
            }
            output.insert(prefix, value);
        }
    }
    Ok(())
}

fn validate_segment(value: &str) -> Result<()> {
    if value.is_empty() || value.contains('\0') {
        anyhow::bail!("configuration contains an invalid key");
    }
    Ok(())
}

fn validate_no_secrets(values: &ManagedValues) -> Result<()> {
    const SECRET_KEYS: &[&str] = &[
        "access_token",
        "api_key",
        "bearer_token",
        "client_secret",
        "password",
        "private_key",
        "refresh_token",
    ];
    for path in values.keys() {
        let key = path
            .last()
            .expect("managed path is non-empty")
            .to_lowercase();
        if key == "env_key" || key.ends_with("_env") {
            continue;
        }
        if SECRET_KEYS.iter().any(|candidate| key.contains(candidate)) {
            anyhow::bail!(
                "refusing to synchronize probable secret at {}; use an environment variable or OS credential store",
                display_path(path)
            );
        }
    }
    Ok(())
}

pub fn render_config(
    current: &str,
    previous_managed_paths: &[Vec<String>],
    desired: &ManagedValues,
) -> Result<String> {
    let mut document = if current.trim().is_empty() {
        DocumentMut::new()
    } else {
        current
            .parse::<DocumentMut>()
            .context("parse current Codex config.toml")?
    };
    let mut old_paths = previous_managed_paths.to_vec();
    old_paths.sort_by_key(|path| std::cmp::Reverse(path.len()));
    for path in old_paths {
        remove_path(document.as_item_mut(), &path);
    }
    for (path, value) in desired {
        set_path(document.as_item_mut(), path, value)?;
    }
    Ok(document.to_string())
}

fn remove_path(item: &mut Item, path: &[String]) -> bool {
    if path.is_empty() {
        return false;
    }
    let Some(table) = item.as_table_like_mut() else {
        return false;
    };
    if path.len() == 1 {
        return table.remove(&path[0]).is_some();
    }
    let Some(child) = table.get_mut(&path[0]) else {
        return false;
    };
    let removed = remove_path(child, &path[1..]);
    if removed
        && child
            .as_table_like()
            .is_some_and(|child_table| child_table.is_empty())
    {
        table.remove(&path[0]);
    }
    removed
}

fn set_path(item: &mut Item, path: &[String], value: &toml::Value) -> Result<()> {
    if path.is_empty() {
        anyhow::bail!("managed configuration path cannot be empty");
    }
    let table = item
        .as_table_like_mut()
        .context("managed configuration parent is not a table")?;
    if path.len() == 1 {
        table.insert(&path[0], value_to_item(value)?);
        return Ok(());
    }
    if !table.contains_key(&path[0]) {
        table.insert(&path[0], Item::Table(toml_edit::Table::new()));
    }
    let child = table
        .get_mut(&path[0])
        .context("managed configuration parent disappeared")?;
    if !child.is_table_like() {
        *child = Item::Table(toml_edit::Table::new());
    }
    set_path(child, &path[1..], value)
}

fn value_to_item(value: &toml::Value) -> Result<Item> {
    let mut wrapper = BTreeMap::new();
    wrapper.insert("codex_sync_value", value.clone());
    let serialized = toml::to_string(&wrapper).context("serialize managed TOML value")?;
    let mut document = serialized
        .parse::<DocumentMut>()
        .context("convert managed TOML value")?;
    document
        .remove("codex_sync_value")
        .context("converted TOML value is missing")
}

pub fn classify_path(path: &[String]) -> Risk {
    let top = path.first().map(String::as_str).unwrap_or_default();
    if matches!(
        top,
        "approval_policy"
            | "approvals_reviewer"
            | "hooks"
            | "mcp_servers"
            | "model_providers"
            | "permissions"
            | "sandbox_mode"
            | "shell_environment_policy"
    ) {
        Risk::High
    } else {
        Risk::Low
    }
}

pub fn changed_paths(
    current: &str,
    rendered: &str,
    desired: &ManagedValues,
    previous_managed_paths: &[Vec<String>],
) -> Vec<Vec<String>> {
    let current_value = current
        .parse::<toml::Value>()
        .unwrap_or_else(|_| toml::Value::Table(toml::map::Map::new()));
    let rendered_value = rendered
        .parse::<toml::Value>()
        .unwrap_or_else(|_| toml::Value::Table(toml::map::Map::new()));
    let mut changes = BTreeSet::new();
    let candidates: BTreeSet<_> = desired
        .keys()
        .cloned()
        .chain(previous_managed_paths.iter().cloned())
        .collect();
    for path in candidates {
        if get_value(&current_value, &path) != get_value(&rendered_value, &path) {
            changes.insert(path);
        }
    }
    changes.into_iter().collect()
}

fn get_value<'a>(value: &'a toml::Value, path: &[String]) -> Option<&'a toml::Value> {
    let mut current = value;
    for segment in path {
        current = current.as_table()?.get(segment)?;
    }
    Some(current)
}

pub fn display_path(path: &[String]) -> String {
    path.iter()
        .map(|segment| {
            if segment.chars().all(|character| {
                character.is_ascii_alphanumeric() || matches!(character, '_' | '-')
            }) {
                segment.clone()
            } else {
                format!("\"{}\"", segment.replace('"', "\\\""))
            }
        })
        .collect::<Vec<_>>()
        .join(".")
}

pub fn read_current_config(codex_home: &Path) -> Result<String> {
    let path = codex_home.join("config.toml");
    match fs::read_to_string(&path) {
        Ok(value) => Ok(value),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(error) => Err(error).with_context(|| format!("read {}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merge_preserves_unmanaged_tables_and_removes_old_managed_values() {
        let current = r#"
model = "old"
[projects."/tmp/example"]
trust_level = "trusted"
[hooks.state]
hash = "device-only"
"#;
        let previous = vec![vec!["model".to_owned()]];
        let mut desired = ManagedValues::new();
        desired.insert(
            vec!["model_reasoning_effort".to_owned()],
            toml::Value::String("high".to_owned()),
        );
        let rendered = render_config(current, &previous, &desired).unwrap();
        assert!(!rendered.contains("model = \"old\""));
        assert!(rendered.contains("model_reasoning_effort = \"high\""));
        assert!(rendered.contains("device-only"));
        assert!(rendered.contains("/tmp/example"));
    }

    #[test]
    fn secret_fields_are_rejected_but_env_key_is_allowed() {
        let mut values = ManagedValues::new();
        values.insert(
            vec!["model_providers".into(), "safe".into(), "env_key".into()],
            toml::Value::String("SAFE_TOKEN".into()),
        );
        assert!(validate_no_secrets(&values).is_ok());
        values.insert(
            vec![
                "model_providers".into(),
                "bad".into(),
                "access_token".into(),
            ],
            toml::Value::String("secret".into()),
        );
        assert!(validate_no_secrets(&values).is_err());
    }
}
