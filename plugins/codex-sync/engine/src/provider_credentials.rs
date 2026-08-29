use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde_json::{Map, Value as JsonValue};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

use crate::codex::InstalledPlugin;

const CACHE_DIRECTORY: &str = ".codex-provider";
const CACHE_FILE: &str = "credential.json";
const CACHE_SCHEMA_VERSION: i64 = 1;
const MAX_CREDENTIAL_BYTES: usize = 512 * 1024;
const TARGET_PLUGIN_NAMES: [&str; 2] = ["provider-chat-completions", "provider-imagegen"];
const FORBIDDEN_HEADERS: [&str; 9] = [
    "connection",
    "content-length",
    "host",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
];

#[derive(Debug, Default)]
pub struct BootstrapReport {
    pub actions: Vec<String>,
}

/// Populate the local cache used by the two provider transport plugins.
///
/// This is deliberately called after Codex configuration and plugin convergence. It reads the
/// resulting config.toml directly, never asks app-server for credentials, and never writes into a
/// marketplace worktree or the Codex Sync Git cache.
pub fn bootstrap_provider_plugins(
    codex_home: &Path,
    desired_plugins: &BTreeSet<String>,
    config_text: &str,
    dry_run: bool,
) -> Result<BootstrapReport> {
    let target_ids = desired_plugins
        .iter()
        .filter(|id| {
            plugin_name(id)
                .map(|name| TARGET_PLUGIN_NAMES.contains(&name))
                .unwrap_or(false)
        })
        .cloned()
        .collect::<Vec<_>>();
    if target_ids.is_empty() {
        return Ok(BootstrapReport::default());
    }

    let installed = crate::codex::installed_plugins()?;
    let material = provider_material(config_text)?;
    let mut report = BootstrapReport::default();

    for id in target_ids {
        let name = plugin_name(&id).context("provider plugin id is missing a name")?;
        let Some(plugin) = installed
            .iter()
            .find(|plugin| plugin.plugin_id == id && plugin.installed)
        else {
            report
                .actions
                .push(format!("bootstrap {name}: plugin_not_installed"));
            continue;
        };
        let Some(root) = plugin_cache_root(codex_home, plugin)? else {
            report
                .actions
                .push(format!("bootstrap {name}: cache_missing"));
            continue;
        };
        let credential_path = root.join(CACHE_DIRECTORY).join(CACHE_FILE);
        let Some(material) = material.as_ref() else {
            if !dry_run {
                cleanup_plugin_caches(codex_home, plugin, None)?;
            }
            report
                .actions
                .push(format!("bootstrap {name}: credential_unavailable"));
            continue;
        };
        if dry_run {
            report.actions.push(format!("bootstrap {name}: ready"));
            continue;
        }
        write_cached_credential(&credential_path, material)?;
        cleanup_plugin_caches(codex_home, plugin, Some(&root))?;
        report.actions.push(format!("bootstrap {name}: ready"));
    }
    Ok(report)
}

fn plugin_name(id: &str) -> Option<&str> {
    id.split_once('@').map(|(name, _)| name)
}

fn provider_material(current: &str) -> Result<Option<JsonValue>> {
    if current.trim().is_empty() {
        return Ok(None);
    }
    let value = current
        .parse::<toml::Value>()
        .context("parse current Codex provider configuration")?;
    let Some(provider_name) = value.get("model_provider").and_then(toml::Value::as_str) else {
        return Ok(None);
    };
    let Some(provider) = value
        .get("model_providers")
        .and_then(toml::Value::as_table)
        .and_then(|providers| providers.get(provider_name))
        .and_then(toml::Value::as_table)
    else {
        return Ok(None);
    };
    let Some(base_url) = provider.get("base_url").and_then(toml::Value::as_str) else {
        return Ok(None);
    };
    if base_url.trim().is_empty() || crate::config::has_embedded_url_credentials(base_url) {
        anyhow::bail!("active provider base_url is invalid for credential cache");
    }
    if base_url
        .chars()
        .any(|character| character.is_control() || character.is_whitespace())
    {
        anyhow::bail!("active provider base_url contains control characters");
    }

    let mut headers = BTreeMap::new();
    if let Some(table) = provider.get("http_headers").and_then(toml::Value::as_table) {
        for (name, value) in table {
            let value = value
                .as_str()
                .context("model provider http_headers values must be strings")?;
            validate_header(name, value)?;
            insert_header(&mut headers, name, value)?;
        }
    }

    let mut env_headers = BTreeMap::new();
    if let Some(table) = provider
        .get("env_http_headers")
        .and_then(toml::Value::as_table)
    {
        for (name, value) in table {
            let env_name = value
                .as_str()
                .context("model provider env_http_headers values must be strings")?;
            if env_name.is_empty() || env_name.chars().any(|character| character.is_control()) {
                anyhow::bail!("provider environment header name is invalid");
            }
            validate_header_name(name)?;
            if header_exists(&headers, name) || header_exists(&env_headers, name) {
                anyhow::bail!("provider headers contain duplicate names");
            }
            env_headers.insert(name.clone(), env_name.to_owned());
        }
    }

    let env_key = provider
        .get("env_key")
        .map(|value| {
            value
                .as_str()
                .filter(|name| {
                    !name.is_empty() && !name.chars().any(|character| character.is_control())
                })
                .map(str::to_owned)
                .context("model provider env_key must be a non-empty string")
        })
        .transpose()?;

    if let Some(value) = provider.get("experimental_bearer_token") {
        let token = value
            .as_str()
            .context("model provider bearer token must be a string")?;
        if token.is_empty()
            || token
                .chars()
                .any(|character| character == '\r' || character == '\n')
        {
            anyhow::bail!("model provider bearer token is invalid");
        }
        if !header_exists(&headers, "Authorization")
            && !header_exists(&env_headers, "Authorization")
        {
            insert_header(&mut headers, "Authorization", &format!("Bearer {token}"))?;
        }
    }

    // Command-backed auth is intentionally outside the cache contract; no arbitrary command is
    // copied into or executed by either provider plugin.
    if provider.get("auth").is_some() {
        return Ok(None);
    }
    let requires_auth = provider
        .get("requires_openai_auth")
        .map(|value| {
            value
                .as_bool()
                .context("requires_openai_auth must be a boolean")
        })
        .transpose()?
        .unwrap_or(false);
    if requires_auth
        && !header_exists(&headers, "Authorization")
        && !header_exists(&env_headers, "Authorization")
        && env_key.is_none()
    {
        return Ok(None);
    }

    let query_params = match provider.get("query_params") {
        Some(value) => {
            let converted = toml_to_json(value)?;
            validate_query_params(&converted)?;
            converted
        }
        None => JsonValue::Object(Map::new()),
    };

    let mut payload = Map::new();
    payload.insert(
        "schema_version".to_owned(),
        JsonValue::Number(CACHE_SCHEMA_VERSION.into()),
    );
    payload.insert(
        "provider".to_owned(),
        JsonValue::String(provider_name.to_owned()),
    );
    payload.insert(
        "base_url".to_owned(),
        JsonValue::String(base_url.to_owned()),
    );
    payload.insert("headers".to_owned(), map_to_json(&headers));
    payload.insert("env_http_headers".to_owned(), map_to_json(&env_headers));
    if let Some(env_key) = env_key {
        payload.insert("env_key".to_owned(), JsonValue::String(env_key));
    }
    payload.insert("query_params".to_owned(), query_params);
    payload.insert(
        "requires_openai_auth".to_owned(),
        JsonValue::Bool(requires_auth),
    );
    let fingerprint = fingerprint(&payload)?;
    payload.insert("fingerprint".to_owned(), JsonValue::String(fingerprint));
    Ok(Some(JsonValue::Object(payload)))
}

fn map_to_json(values: &BTreeMap<String, String>) -> JsonValue {
    JsonValue::Object(
        values
            .iter()
            .map(|(key, value)| (key.clone(), JsonValue::String(value.clone())))
            .collect(),
    )
}

fn toml_to_json(value: &toml::Value) -> Result<JsonValue> {
    Ok(match value {
        toml::Value::String(value) => JsonValue::String(value.clone()),
        toml::Value::Integer(value) => JsonValue::Number((*value).into()),
        toml::Value::Float(value) => serde_json::Number::from_f64(*value)
            .map(JsonValue::Number)
            .context("provider query parameter contains a non-finite float")?,
        toml::Value::Boolean(value) => JsonValue::Bool(*value),
        toml::Value::Datetime(value) => JsonValue::String(value.to_string()),
        toml::Value::Array(values) => JsonValue::Array(
            values
                .iter()
                .map(toml_to_json)
                .collect::<Result<Vec<_>>>()?,
        ),
        toml::Value::Table(table) => JsonValue::Object(
            table
                .iter()
                .map(|(key, value)| Ok((key.clone(), toml_to_json(value)?)))
                .collect::<Result<Map<_, _>>>()?,
        ),
    })
}

fn validate_query_params(value: &JsonValue) -> Result<()> {
    let Some(table) = value.as_object() else {
        anyhow::bail!("provider query_params must be a table")
    };
    for (key, value) in table {
        let normalized = key.to_ascii_lowercase().replace('-', "_");
        if [
            "api_key",
            "apikey",
            "authorization",
            "password",
            "secret",
            "token",
        ]
        .iter()
        .any(|part| normalized.contains(part))
        {
            anyhow::bail!("provider query_params contains a credential name");
        }
        if value.is_object() || value.is_null() {
            anyhow::bail!("provider query_params values must be scalar or arrays");
        }
        if let Some(values) = value.as_array() {
            if values.iter().any(JsonValue::is_object) {
                anyhow::bail!("provider query_params arrays must contain scalar values");
            }
        }
    }
    Ok(())
}

fn fingerprint(payload: &Map<String, JsonValue>) -> Result<String> {
    let bytes = serde_json::to_vec(payload).context("serialize provider credential fingerprint")?;
    let digest = Sha256::digest(bytes);
    Ok(format!("{digest:x}"))
}

fn validate_header_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.bytes().any(|byte| {
            !(byte.is_ascii_alphanumeric()
                || matches!(
                    byte,
                    b'!' | b'#'
                        | b'$'
                        | b'%'
                        | b'&'
                        | b'\''
                        | b'*'
                        | b'+'
                        | b'-'
                        | b'.'
                        | b'^'
                        | b'_'
                        | b'`'
                        | b'|'
                        | b'~'
                ))
        })
    {
        anyhow::bail!("provider header name is invalid");
    }
    let lowered = name.to_ascii_lowercase();
    if FORBIDDEN_HEADERS.contains(&lowered.as_str()) || lowered.starts_with("proxy-") {
        anyhow::bail!("provider header name is not allowed");
    }
    Ok(())
}

fn validate_header(name: &str, value: &str) -> Result<()> {
    validate_header_name(name)?;
    if value.len() > MAX_CREDENTIAL_BYTES
        || value
            .chars()
            .any(|character| character == '\r' || character == '\n')
    {
        anyhow::bail!("provider header value is invalid");
    }
    Ok(())
}

fn insert_header(headers: &mut BTreeMap<String, String>, name: &str, value: &str) -> Result<()> {
    if header_exists(headers, name) {
        anyhow::bail!("provider headers contain duplicate names");
    }
    headers.insert(name.to_owned(), value.to_owned());
    Ok(())
}

fn header_exists(headers: &BTreeMap<String, String>, name: &str) -> bool {
    headers
        .keys()
        .any(|candidate| candidate.eq_ignore_ascii_case(name))
}

fn plugin_cache_root(codex_home: &Path, plugin: &InstalledPlugin) -> Result<Option<PathBuf>> {
    let (cache, plugin_path) = match plugin_cache_paths(codex_home, plugin)? {
        Some(paths) => paths,
        None => return Ok(None),
    };
    let source_path = plugin
        .source
        .as_ref()
        .and_then(|source| source.get("path"))
        .and_then(serde_json::Value::as_str);
    let expected = if let Some(version) = plugin.version.as_deref() {
        if !safe_path_component(version) {
            anyhow::bail!("installed provider plugin version is invalid");
        }
        existing_cache_directory(&cache, &plugin_path.join(version))?
    } else {
        None
    };
    let source = match source_path.map(PathBuf::from) {
        Some(source) => match fs::symlink_metadata(&source) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                anyhow::bail!("provider plugin source path must not be a symlink")
            }
            Ok(metadata) if metadata.file_type().is_dir() => {
                let canonical =
                    fs::canonicalize(&source).context("canonicalize provider plugin cache")?;
                canonical.starts_with(&cache).then_some(canonical)
            }
            Ok(_) => None,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
            Err(error) => return Err(error).context("inspect provider plugin source path"),
        },
        None => None,
    };
    let root = match (expected, source) {
        (Some(expected), Some(source)) if source != expected => {
            anyhow::bail!("provider plugin source path does not match installed version")
        }
        (Some(expected), _) => expected,
        (None, Some(source))
            if source
                .parent()
                .is_some_and(|parent| parent == plugin_path.as_path()) =>
        {
            source
        }
        (None, Some(_)) => {
            anyhow::bail!("provider plugin source path is not an exact cache version")
        }
        (None, None) => return Ok(None),
    };
    Ok(Some(root))
}

fn plugin_cache_paths(
    codex_home: &Path,
    plugin: &InstalledPlugin,
) -> Result<Option<(PathBuf, PathBuf)>> {
    crate::codex::validate_plugin_id(&plugin.plugin_id)?;
    let plugins = codex_home.join("plugins");
    match fs::symlink_metadata(&plugins) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("Codex plugin directory must not be a symlink")
        }
        Ok(_) => anyhow::bail!("Codex plugin directory is not a directory"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("inspect Codex plugin directory"),
    }
    let cache = plugins.join("cache");
    match fs::symlink_metadata(&cache) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("Codex plugin cache must not be a symlink")
        }
        Ok(_) => anyhow::bail!("Codex plugin cache is not a directory"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("inspect Codex plugin cache"),
    }
    let cache = fs::canonicalize(&cache).context("canonicalize Codex plugin cache")?;
    ensure_not_git_worktree(&cache)?;
    let Some(name) = plugin_name(&plugin.plugin_id) else {
        return Ok(None);
    };
    let market = plugin
        .plugin_id
        .split_once('@')
        .map(|(_, market)| market)
        .context("plugin marketplace is missing")?;
    if !safe_path_component(name) || !safe_path_component(market) {
        anyhow::bail!("installed provider plugin path component is invalid");
    }
    let plugin_path = cache.join(market).join(name);
    let metadata = match fs::symlink_metadata(&plugin_path) {
        Ok(metadata) if metadata.file_type().is_dir() => metadata,
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("provider plugin cache path must not be a symlink")
        }
        Ok(_) => anyhow::bail!("provider plugin cache path is not a directory"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("inspect provider plugin cache path"),
    };
    let _ = metadata;
    let plugin_path =
        fs::canonicalize(&plugin_path).context("canonicalize provider plugin path")?;
    if !plugin_path.starts_with(&cache) {
        anyhow::bail!("provider plugin cache escapes Codex cache root");
    }
    Ok(Some((cache, plugin_path)))
}

fn cleanup_plugin_caches(
    codex_home: &Path,
    plugin: &InstalledPlugin,
    keep: Option<&Path>,
) -> Result<()> {
    let Some((cache, _current_plugin_path)) = plugin_cache_paths(codex_home, plugin)? else {
        return Ok(());
    };
    let Some(name) = plugin_name(&plugin.plugin_id) else {
        return Ok(());
    };
    for market_entry in fs::read_dir(&cache).context("read Codex plugin cache marketplaces")? {
        let market_entry = market_entry?;
        if market_entry.file_type()?.is_symlink() {
            anyhow::bail!("provider plugin marketplace cache must not be a symlink");
        }
        if !market_entry.file_type()?.is_dir() {
            continue;
        }
        let plugin_path = market_entry.path().join(name);
        let metadata = match fs::symlink_metadata(&plugin_path) {
            Ok(metadata) if metadata.file_type().is_dir() => metadata,
            Ok(metadata) if metadata.file_type().is_symlink() => {
                anyhow::bail!("provider plugin cache path must not be a symlink")
            }
            Ok(_) => anyhow::bail!("provider plugin cache path is not a directory"),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error).context("inspect provider plugin cache path"),
        };
        let _ = metadata;
        let plugin_path =
            fs::canonicalize(&plugin_path).context("canonicalize provider plugin path")?;
        if !plugin_path.starts_with(&cache) {
            anyhow::bail!("provider plugin cache escapes Codex cache root");
        }
        for version_entry in
            fs::read_dir(&plugin_path).context("read provider plugin cache versions")?
        {
            let version_entry = version_entry?;
            if version_entry.file_type()?.is_symlink() {
                anyhow::bail!("provider plugin cache version must not be a symlink");
            }
            if !version_entry.file_type()?.is_dir() {
                continue;
            }
            let version_root = existing_cache_directory(&cache, &version_entry.path())?;
            let Some(version_root) = version_root else {
                continue;
            };
            if keep.is_some_and(|path| path == version_root.as_path()) {
                continue;
            }
            remove_cached_credential(&version_root.join(CACHE_DIRECTORY).join(CACHE_FILE))?;
        }
    }
    Ok(())
}

fn ensure_not_git_worktree(path: &Path) -> Result<()> {
    let mut current = Some(path);
    while let Some(directory) = current {
        match fs::symlink_metadata(directory.join(".git")) {
            Ok(_) => anyhow::bail!("provider plugin cache must not be inside a Git worktree"),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error).context("inspect provider plugin cache ancestry"),
        }
        current = directory.parent();
    }
    Ok(())
}

fn existing_cache_directory(cache: &Path, candidate: &Path) -> Result<Option<PathBuf>> {
    let metadata = match fs::symlink_metadata(candidate) {
        Ok(metadata) if metadata.file_type().is_dir() => metadata,
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("provider plugin cache version must not be a symlink")
        }
        Ok(_) => anyhow::bail!("provider plugin cache version is not a directory"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("inspect provider plugin cache version"),
    };
    let _ = metadata;
    let canonical =
        fs::canonicalize(candidate).context("canonicalize provider plugin cache version")?;
    if !canonical.starts_with(cache) {
        anyhow::bail!("provider plugin cache escapes Codex cache root");
    }
    Ok(Some(canonical))
}

fn safe_path_component(value: &str) -> bool {
    !value.is_empty()
        && value != "."
        && value != ".."
        && !value.chars().any(|character| {
            character == '/' || character == '\\' || character == '\0' || character.is_control()
        })
}

fn write_cached_credential(path: &Path, material: &JsonValue) -> Result<()> {
    let bytes = serde_json::to_vec(material).context("serialize provider credential cache")?;
    if bytes.len() > MAX_CREDENTIAL_BYTES {
        anyhow::bail!("provider credential cache is too large");
    }
    let parent = path
        .parent()
        .context("provider credential cache has no parent")?;
    ensure_cache_directory(parent)?;
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
            anyhow::bail!("provider credential cache file is not a regular file");
        }
    }
    let mut temporary =
        NamedTempFile::new_in(parent).context("create provider credential cache")?;
    std::io::Write::write_all(&mut temporary, &bytes).context("write provider credential cache")?;
    temporary
        .as_file()
        .sync_all()
        .context("sync provider credential cache")?;
    temporary
        .persist(path)
        .map_err(|error| error.error)
        .context("replace provider credential cache")?;
    Ok(())
}

fn remove_cached_credential(path: &Path) -> Result<()> {
    if let Some(parent) = path.parent() {
        match fs::symlink_metadata(parent) {
            Ok(metadata) if metadata.file_type().is_dir() => {}
            Ok(metadata) if metadata.file_type().is_symlink() => {
                anyhow::bail!("refusing to inspect symlink provider credential directory")
            }
            Ok(_) => anyhow::bail!("provider credential directory is not a directory"),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(error).context("inspect provider credential directory"),
        }
    }
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() => {
            fs::remove_file(path).context("remove stale provider credential cache")?
        }
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("refusing to remove symlink provider credential cache")
        }
        Ok(_) => anyhow::bail!("provider credential cache is not a regular file"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error).context("inspect provider credential cache"),
    }
    Ok(())
}

fn ensure_cache_directory(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("provider credential directory must not be a symlink")
        }
        Ok(_) => anyhow::bail!("provider credential path is not a directory"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir_all(path).context("create provider credential directory")?
        }
        Err(error) => return Err(error).context("inspect provider credential directory"),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn material_resolves_literal_bearer_without_persisting_legacy_field() {
        let current = r#"
model_provider = "company"
[model_providers.company]
base_url = "https://provider.example/v1"
experimental_bearer_token = "secret-token"
query_params = { tenant = "one" }
"#;
        let material = provider_material(current).unwrap().unwrap();
        assert_eq!(material["headers"]["Authorization"], "Bearer secret-token");
        assert!(material.get("experimental_bearer_token").is_none());
        assert!(material["fingerprint"].as_str().unwrap().len() == 64);
    }

    #[test]
    fn material_keeps_environment_references_when_the_value_is_not_local() {
        let current = r#"
model_provider = "company"
[model_providers.company]
base_url = "https://provider.example/v1"
env_key = "COMPANY_TOKEN"
[model_providers.company.env_http_headers]
"X-Tenant" = "COMPANY_TENANT"
"#;
        let material = provider_material(current).unwrap().unwrap();
        assert_eq!(material["env_key"], "COMPANY_TOKEN");
        assert_eq!(material["env_http_headers"]["X-Tenant"], "COMPANY_TENANT");
    }

    #[test]
    fn cache_write_creates_a_regular_file() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join(".codex-provider/credential.json");
        write_cached_credential(&path, &serde_json::json!({"schema_version": 1})).unwrap();
        assert!(fs::metadata(path.parent().unwrap()).unwrap().is_dir());
        assert!(fs::metadata(path).unwrap().is_file());
    }
}
