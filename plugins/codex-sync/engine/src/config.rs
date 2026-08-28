use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use toml_edit::{DocumentMut, Item, TableLike};

use crate::storage::atomic_write;

pub type ManagedValues = BTreeMap<Vec<String>, toml::Value>;

const ACTOR_AUTHORIZATION_HEADER: &str = "x-openai-actor-authorization";
const CODE_MODE_DIRECT_ONLY_PATH: [&str; 3] =
    ["features", "code_mode", "direct_only_tool_namespaces"];

const SECRET_PARTS: &[&str] = &[
    "access_token",
    "api_key",
    "apikey",
    "bearer_token",
    "client_secret",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "refresh_token",
    "secret",
];

pub fn flatten(value: &toml::Value) -> Result<ManagedValues> {
    let mut result = ManagedValues::new();
    flatten_into(&mut result, Vec::new(), value)?;
    Ok(result)
}

fn flatten_into(
    output: &mut ManagedValues,
    prefix: Vec<String>,
    value: &toml::Value,
) -> Result<()> {
    match value {
        toml::Value::Table(table) => {
            for (key, child) in table {
                if key.is_empty() || key.contains('\0') {
                    anyhow::bail!("configuration contains an invalid key");
                }
                let mut path = prefix.clone();
                path.push(key.clone());
                flatten_into(output, path, child)?;
            }
        }
        child => {
            if prefix.is_empty() {
                anyhow::bail!("configuration root must be a TOML table");
            }
            output.insert(prefix, child.clone());
        }
    }
    Ok(())
}

pub fn load_managed_values(repository: &Path, device: &str) -> Result<ManagedValues> {
    let mut result = ManagedValues::new();
    let common_path = repository.join("config/common.toml");
    let common: toml::Value = match fs::read_to_string(&common_path) {
        Ok(text) => {
            toml::from_str(&text).with_context(|| format!("parse {}", common_path.display()))?
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            toml::Value::Table(toml::map::Map::new())
        }
        Err(error) => return Err(error).with_context(|| format!("read {}", common_path.display())),
    };
    for (path, value) in flatten(&common)? {
        result.insert(path, value);
    }
    let device_file = repository.join("devices").join(format!("{device}.toml"));
    match fs::read_to_string(&device_file) {
        Ok(text) => {
            let device_value: toml::Value = toml::from_str(&text)
                .with_context(|| format!("parse {}", device_file.display()))?;
            for (path, value) in flatten(&device_value)? {
                result.insert(path, value);
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error).with_context(|| format!("read {}", device_file.display())),
    }
    validate_values(&result)?;
    Ok(result)
}

pub fn validate_values(values: &ManagedValues) -> Result<()> {
    for (path, value) in values {
        let allowed_bearer = path.len() == 3
            && path[0] == "model_providers"
            && path[2] == "experimental_bearer_token";
        let allowed_actor = path.len() == 4
            && path[0] == "model_providers"
            && path[2] == "http_headers"
            && path[3].eq_ignore_ascii_case(ACTOR_AUTHORIZATION_HEADER);
        let allowed_env_header =
            path.len() == 4 && path[0] == "model_providers" && path[2] == "env_http_headers";
        let normalized_key = path
            .last()
            .map(String::as_str)
            .unwrap_or_default()
            .to_ascii_lowercase()
            .replace('-', "_");
        if !allowed_bearer
            && !allowed_actor
            && !allowed_env_header
            && normalized_key != "env_key"
            && !normalized_key.ends_with("_env")
            && SECRET_PARTS
                .iter()
                .any(|part| normalized_key.contains(part))
        {
            anyhow::bail!(
                "refusing to synchronize probable secret at {}",
                display_path(path)
            );
        }
        validate_value_strings(value, path)?;
        if allowed_bearer {
            let Some(token) = value.as_str() else {
                anyhow::bail!("model_providers.*.experimental_bearer_token must be a string");
            };
            if token.trim().is_empty() {
                anyhow::bail!("model_providers.*.experimental_bearer_token must be non-empty");
            }
        }
    }
    Ok(())
}

fn validate_value_strings(value: &toml::Value, path: &[String]) -> Result<()> {
    match value {
        toml::Value::String(text) => {
            if has_embedded_url_credentials(text) {
                anyhow::bail!(
                    "refusing to synchronize URL with embedded credentials at {}",
                    display_path(path)
                );
            }
        }
        toml::Value::Array(values) => {
            for child in values {
                validate_value_strings(child, path)?;
            }
        }
        toml::Value::Table(table) => {
            for (key, child) in table {
                let mut nested = path.to_vec();
                nested.push(key.clone());
                validate_value_strings(child, &nested)?;
            }
        }
        _ => {}
    }
    Ok(())
}

pub fn has_embedded_url_credentials(value: &str) -> bool {
    for prefix in ["http://", "https://", "ssh://"] {
        let mut offset = 0;
        while let Some(relative) = value[offset..].find(prefix) {
            let start = offset + relative + prefix.len();
            let authority = value[start..]
                .split(['/', ' ', '\n', '\r', '"', '\'', ']', ')', ','])
                .next()
                .unwrap_or_default();
            if authority.contains('@') {
                return true;
            }
            offset = start;
            if offset >= value.len() {
                break;
            }
        }
    }
    false
}

pub fn read_current(codex_home: &Path) -> Result<String> {
    match fs::read_to_string(codex_home.join("config.toml")) {
        Ok(value) => Ok(value),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(error) => Err(error).context("read Codex config.toml"),
    }
}

pub fn read_optional_value(path: &Path) -> Result<toml::Value> {
    match fs::read_to_string(path) {
        Ok(text) => toml::from_str(&text).with_context(|| format!("parse {}", path.display())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            Ok(toml::Value::Table(toml::map::Map::new()))
        }
        Err(error) => Err(error).with_context(|| format!("read {}", path.display())),
    }
}

pub fn render_config(
    current: &str,
    previous: &[Vec<String>],
    desired: &ManagedValues,
) -> Result<String> {
    let mut document = if current.trim().is_empty() {
        DocumentMut::new()
    } else {
        current
            .parse::<DocumentMut>()
            .context("parse Codex config.toml")?
    };
    let mut paths = previous.to_vec();
    paths.sort_by_key(|path| std::cmp::Reverse(path.len()));
    for path in paths {
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
    if removed && child.as_table_like().is_some_and(TableLike::is_empty) {
        table.remove(&path[0]);
    }
    removed
}

fn set_path(item: &mut Item, path: &[String], value: &toml::Value) -> Result<()> {
    if path.is_empty() {
        anyhow::bail!("managed path cannot be empty");
    }
    let table = item
        .as_table_like_mut()
        .context("configuration parent is not a table")?;
    if path.len() == 1 {
        table.insert(&path[0], value_to_item(value)?);
        return Ok(());
    }
    if !table.contains_key(&path[0]) {
        table.insert(&path[0], Item::Table(toml_edit::Table::new()));
    }
    let child = table
        .get_mut(&path[0])
        .context("configuration parent disappeared")?;
    if !child.is_table_like() {
        *child = Item::Table(toml_edit::Table::new());
    }
    set_path(child, &path[1..], value)
}

fn value_to_item(value: &toml::Value) -> Result<Item> {
    let mut wrapper = toml::map::Map::new();
    wrapper.insert("value".to_owned(), value.clone());
    let text = toml::to_string(&toml::Value::Table(wrapper))?;
    let mut document = text.parse::<DocumentMut>()?;
    document.remove("value").context("convert TOML value")
}

pub fn leaf_paths(value: &toml::Value) -> Vec<Vec<String>> {
    let mut result = Vec::new();
    collect_leaf_paths(value, Vec::new(), &mut result);
    result
}

fn collect_leaf_paths(value: &toml::Value, prefix: Vec<String>, output: &mut Vec<Vec<String>>) {
    if let Some(table) = value.as_table() {
        for (key, child) in table {
            let mut path = prefix.clone();
            path.push(key.clone());
            collect_leaf_paths(child, path, output);
        }
    } else if !prefix.is_empty() {
        output.push(prefix);
    }
}

pub fn value_at<'a>(value: &'a toml::Value, path: &[String]) -> Option<&'a toml::Value> {
    let mut current = value;
    for segment in path {
        current = current.as_table()?.get(segment)?;
    }
    Some(current)
}

/// Return the narrowly allowlisted capability paths that may be declared
/// automatically during capture. All other newly discovered local keys remain
/// unmanaged and are reported without being synchronized.
pub fn auto_capture_paths(value: &toml::Value) -> Vec<Vec<String>> {
    leaf_paths(value)
        .into_iter()
        .filter(|path| {
            let actor_header = path.len() == 4
                && path[0] == "model_providers"
                && path[2] == "http_headers"
                && path[3].eq_ignore_ascii_case(ACTOR_AUTHORIZATION_HEADER)
                && value_at(value, path)
                    .and_then(toml::Value::as_str)
                    .is_some_and(|header| !header.trim().is_empty());
            let direct_only_namespaces = path
                .iter()
                .map(String::as_str)
                .eq(CODE_MODE_DIRECT_ONLY_PATH)
                && value_at(value, path)
                    .and_then(toml::Value::as_array)
                    .is_some_and(|namespaces| {
                        namespaces.iter().all(|namespace| {
                            namespace
                                .as_str()
                                .is_some_and(|name| !name.trim().is_empty())
                        })
                    });
            actor_header || direct_only_namespaces
        })
        .collect()
}

pub fn capture_declared(
    current: &str,
    target: &Path,
    declared: &[Vec<String>],
) -> Result<Vec<Vec<String>>> {
    let current_value = if current.trim().is_empty() {
        toml::Value::Table(toml::map::Map::new())
    } else {
        current
            .parse::<toml::Value>()
            .context("parse current Codex config.toml")?
    };
    let previous_text = match fs::read_to_string(target) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(error) => return Err(error).with_context(|| format!("read {}", target.display())),
    };
    let mut document = if previous_text.trim().is_empty() {
        DocumentMut::new()
    } else {
        previous_text
            .parse::<DocumentMut>()
            .context("parse synchronized config")?
    };
    let mut kept = Vec::new();
    for path in declared {
        if let Some(value) = value_at(&current_value, path) {
            set_path(document.as_item_mut(), path, value)?;
            kept.push(path.clone());
        } else {
            remove_path(document.as_item_mut(), path);
        }
    }
    let rendered = document.to_string();
    if rendered != previous_text {
        atomic_write(target, rendered.as_bytes())?;
    }
    Ok(kept)
}

pub fn display_path(path: &[String]) -> String {
    path.iter()
        .map(|segment| {
            if segment
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-'))
            {
                segment.clone()
            } else {
                format!("\"{}\"", segment.replace('"', "\\\""))
            }
        })
        .collect::<Vec<_>>()
        .join(".")
}

pub fn unmanaged_paths(
    current: &str,
    declared: &BTreeSet<Vec<String>>,
) -> Result<Vec<Vec<String>>> {
    let value = if current.trim().is_empty() {
        toml::Value::Table(toml::map::Map::new())
    } else {
        current
            .parse::<toml::Value>()
            .context("parse current Codex config.toml")?
    };
    Ok(leaf_paths(&value)
        .into_iter()
        .filter(|path| !declared.contains(path))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_url_occurrence_is_scanned() {
        assert!(has_embedded_url_credentials(
            "https://safe.example/v1 and https://user:pass@example.test/v2"
        ));
        let mut values = ManagedValues::new();
        values.insert(
            vec!["urls".into()],
            toml::Value::Array(vec![
                toml::Value::String("https://safe.example".into()),
                toml::Value::String("https://user:pass@example.test".into()),
            ]),
        );
        assert!(validate_values(&values).is_err());
    }

    #[test]
    fn bearer_exception_is_exact_and_non_empty() {
        let mut values = ManagedValues::new();
        values.insert(
            vec![
                "model_providers".into(),
                "company".into(),
                "experimental_bearer_token".into(),
            ],
            toml::Value::String("token".into()),
        );
        assert!(validate_values(&values).is_ok());
        values.insert(
            vec!["other".into(), "experimental_bearer_token".into()],
            toml::Value::String("token".into()),
        );
        assert!(validate_values(&values).is_err());
    }

    #[test]
    fn environment_header_references_are_not_treated_as_literal_secrets() {
        let mut values = ManagedValues::new();
        values.insert(
            vec![
                "model_providers".into(),
                "company".into(),
                "env_http_headers".into(),
                "Authorization".into(),
            ],
            toml::Value::String("COMPANY_TOKEN".into()),
        );
        assert!(validate_values(&values).is_ok());
    }

    #[test]
    fn auto_capture_only_allows_nonempty_actor_authorization_header() {
        let value: toml::Value = toml::from_str(
            r#"
            [model_providers.cpa.http_headers]
            "x-openai-actor-authorization" = "custom"
            "authorization" = "do-not-capture"
            "x-empty" = ""
            "#,
        )
        .unwrap();
        assert_eq!(
            auto_capture_paths(&value),
            vec![vec![
                "model_providers".to_owned(),
                "cpa".to_owned(),
                "http_headers".to_owned(),
                "x-openai-actor-authorization".to_owned(),
            ]]
        );
    }

    #[test]
    fn auto_capture_allows_direct_only_namespace_list() {
        let value: toml::Value = toml::from_str(
            r#"
            [features.code_mode]
            direct_only_tool_namespaces = ["image_gen"]
            "#,
        )
        .unwrap();
        assert_eq!(
            auto_capture_paths(&value),
            vec![vec![
                "features".to_owned(),
                "code_mode".to_owned(),
                "direct_only_tool_namespaces".to_owned(),
            ]]
        );
    }
}
