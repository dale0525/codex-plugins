use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::github::GithubClient;
use crate::model::{MarketplaceSpec, PluginSpec, RepositoryRef};

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstalledPlugin {
    pub plugin_id: String,
    pub installed: bool,
    pub enabled: bool,
}

#[derive(Debug, Deserialize)]
struct PluginList {
    #[serde(default)]
    installed: Vec<InstalledPlugin>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MarketplaceList {
    #[serde(default)]
    marketplaces: Vec<ConfiguredMarketplace>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ConfiguredMarketplace {
    name: String,
    marketplace_source: Option<ConfiguredMarketplaceSource>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ConfiguredMarketplaceSource {
    source_type: String,
    source: String,
}

pub fn marketplace_names() -> Result<BTreeSet<String>> {
    Ok(marketplace_roots()?.into_keys().collect())
}

pub fn marketplace_roots() -> Result<BTreeMap<String, PathBuf>> {
    let output = codex_output(&["plugin", "marketplace", "list"])?;
    let text = String::from_utf8(output.stdout).context("marketplace list is not UTF-8")?;
    let mut values = BTreeMap::new();
    for line in text.lines().skip(1) {
        let Some(index) = line.find(char::is_whitespace) else {
            continue;
        };
        let name = line[..index].trim();
        let root = line[index..].trim();
        if !name.is_empty() && !root.is_empty() {
            values.insert(name.to_owned(), PathBuf::from(root));
        }
    }
    Ok(values)
}

fn configured_git_marketplaces() -> Result<BTreeMap<String, String>> {
    let output = codex_output(&["plugin", "marketplace", "list", "--json"])?;
    let parsed: MarketplaceList =
        serde_json::from_slice(&output.stdout).context("parse Codex marketplace list")?;
    Ok(parsed
        .marketplaces
        .into_iter()
        .filter_map(|marketplace| {
            let source = marketplace.marketplace_source?;
            (source.source_type == "git").then_some((marketplace.name, source.source))
        })
        .collect())
}

pub fn remove_marketplace(name: &str) -> Result<()> {
    if !portable_name(name) {
        anyhow::bail!("invalid marketplace name: {name}");
    }
    codex_output(&["plugin", "marketplace", "remove", name])?;
    Ok(())
}

pub fn add_local_marketplace(root: &Path) -> Result<()> {
    let root = root
        .to_str()
        .context("marketplace root is not valid UTF-8")?;
    codex_output(&["plugin", "marketplace", "add", root])?;
    Ok(())
}

pub fn installed_plugins() -> Result<Vec<InstalledPlugin>> {
    let output = codex_output(&["plugin", "list", "--json"])?;
    let parsed: PluginList =
        serde_json::from_slice(&output.stdout).context("parse Codex plugin list")?;
    Ok(parsed.installed)
}

pub fn reconcile_marketplaces(
    specs: &[MarketplaceSpec],
    github: &GithubClient,
    marketplace_root: &Path,
) -> Result<Vec<String>> {
    let mut configured = marketplace_roots()?;
    let configured_git = if specs
        .iter()
        .any(|spec| matches!(spec, MarketplaceSpec::Git { .. }))
    {
        configured_git_marketplaces()?
    } else {
        BTreeMap::new()
    };
    let mut messages = Vec::new();
    for spec in specs {
        match spec {
            MarketplaceSpec::Git {
                name,
                url,
                git_ref,
                sparse,
            } => {
                if configured_git.get(name).is_some_and(|source| source == url) {
                    codex_output(&["plugin", "marketplace", "upgrade", name])?;
                    messages.push(format!("refreshed marketplace {name}"));
                    continue;
                }
                if configured.contains_key(name) {
                    codex_output(&["plugin", "marketplace", "remove", name])?;
                }
                let mut arguments = vec![
                    "plugin".to_owned(),
                    "marketplace".to_owned(),
                    "add".to_owned(),
                    url.clone(),
                    "--ref".to_owned(),
                    git_ref.clone(),
                ];
                for path in sparse {
                    arguments.push("--sparse".to_owned());
                    arguments.push(path.clone());
                }
                codex_owned(&arguments)?;
                configured.insert(name.clone(), PathBuf::new());
                messages.push(format!(
                    "registered marketplace {name} from the declared source"
                ));
                codex_output(&["plugin", "marketplace", "upgrade", name])?;
                messages.push(format!("refreshed marketplace {name}"));
            }
            MarketplaceSpec::GithubSnapshot {
                name,
                repository,
                git_ref,
            } => {
                let reference = RepositoryRef::parse(repository, git_ref.clone())?;
                let commit = github.resolve_commit(&reference)?;
                let destination = marketplace_root.join(name).join(&commit);
                if !destination.exists() {
                    github.download_repository(&reference, &commit, &destination)?;
                }
                validate_marketplace_snapshot(name, &destination)?;
                let registered_destination = configured.get(name).is_some_and(|root| {
                    root == &destination
                        || (root.exists()
                            && fs::canonicalize(root).ok() == fs::canonicalize(&destination).ok())
                });
                if !registered_destination {
                    if configured.contains_key(name) {
                        codex_output(&["plugin", "marketplace", "remove", name])?;
                    }
                    let snapshot = destination.to_string_lossy().into_owned();
                    codex_output(&["plugin", "marketplace", "add", &snapshot])?;
                    configured.insert(name.clone(), destination.clone());
                    messages.push(format!("registered private marketplace {name} at {commit}"));
                } else {
                    messages.push(format!("private marketplace {name} is current at {commit}"));
                }
            }
        }
    }
    Ok(messages)
}

fn validate_marketplace_snapshot(expected_name: &str, root: &Path) -> Result<()> {
    let path = root.join(".agents/plugins/marketplace.json");
    let bytes = fs::read(&path)
        .with_context(|| format!("private marketplace is missing {}", path.display()))?;
    let value: serde_json::Value =
        serde_json::from_slice(&bytes).with_context(|| format!("parse {}", path.display()))?;
    if value.get("name").and_then(serde_json::Value::as_str) != Some(expected_name) {
        anyhow::bail!(
            "private marketplace name in {} does not match {}",
            path.display(),
            expected_name
        );
    }
    Ok(())
}

pub fn plugin_ids_to_remove(
    installed: &[InstalledPlugin],
    specs: &[PluginSpec],
    managed_marketplaces: &BTreeSet<String>,
) -> Result<Vec<String>> {
    let desired_ids: BTreeSet<_> = specs.iter().map(|spec| spec.id.as_str()).collect();
    let mut removals = Vec::new();
    for plugin in installed.iter().filter(|plugin| plugin.installed) {
        let marketplace = plugin_marketplace(&plugin.plugin_id)?;
        if managed_marketplaces.contains(marketplace)
            && !is_openai_managed_marketplace(marketplace)
            && !desired_ids.contains(plugin.plugin_id.as_str())
        {
            removals.push(plugin.plugin_id.clone());
        }
    }
    Ok(removals)
}

pub fn reconcile_plugins(
    specs: &[PluginSpec],
    managed_marketplaces: &BTreeSet<String>,
) -> Result<Vec<String>> {
    let installed = installed_plugins()?;
    let by_id: BTreeMap<_, _> = installed
        .iter()
        .map(|plugin| (plugin.plugin_id.as_str(), plugin))
        .collect();
    let mut messages = Vec::new();
    for plugin_id in plugin_ids_to_remove(&installed, specs, managed_marketplaces)? {
        codex_output(&["plugin", "remove", &plugin_id])?;
        messages.push(format!("removed plugin {plugin_id}"));
    }
    for spec in specs {
        validate_plugin_id(&spec.id)?;
        let current = by_id.get(spec.id.as_str());
        if spec.enabled {
            if current.is_none_or(|plugin| !plugin.installed || !plugin.enabled) {
                codex_output(&["plugin", "add", &spec.id])?;
                messages.push(format!("installed plugin {}", spec.id));
            }
        } else if current.is_some_and(|plugin| plugin.installed) {
            codex_output(&["plugin", "remove", &spec.id])?;
            messages.push(format!("removed plugin {}", spec.id));
        }
    }
    Ok(messages)
}

pub fn restore_installed_plugins(snapshot: &[InstalledPlugin]) -> Result<()> {
    let current = installed_plugins()?;
    let expected_ids: BTreeSet<_> = snapshot
        .iter()
        .filter(|plugin| plugin.installed)
        .map(|plugin| plugin.plugin_id.as_str())
        .collect();
    for plugin in &current {
        if plugin.installed && !expected_ids.contains(plugin.plugin_id.as_str()) {
            codex_output(&["plugin", "remove", &plugin.plugin_id])?;
        }
    }
    let current_by_id: BTreeMap<_, _> = current
        .iter()
        .map(|plugin| (plugin.plugin_id.as_str(), plugin))
        .collect();
    for plugin in snapshot.iter().filter(|plugin| plugin.installed) {
        if current_by_id
            .get(plugin.plugin_id.as_str())
            .is_none_or(|current| !current.installed)
        {
            codex_output(&["plugin", "add", &plugin.plugin_id])?;
        }
    }
    Ok(())
}

pub fn validate_plugin_id(value: &str) -> Result<()> {
    let Some((plugin, marketplace)) = value.split_once('@') else {
        anyhow::bail!("plugin id must use plugin@marketplace syntax: {value}");
    };
    if !portable_name(plugin) || !portable_name(marketplace) {
        anyhow::bail!("invalid plugin id: {value}");
    }
    Ok(())
}

pub fn plugin_marketplace(value: &str) -> Result<&str> {
    validate_plugin_id(value)?;
    Ok(value
        .split_once('@')
        .expect("validated plugin ID contains a marketplace")
        .1)
}

pub fn is_openai_managed_marketplace(value: &str) -> bool {
    value == "openai" || value.starts_with("openai-")
}

pub fn portable_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.split('-').all(|segment| {
            !segment.is_empty()
                && segment
                    .chars()
                    .all(|character| character.is_ascii_lowercase() || character.is_ascii_digit())
        })
}

pub fn verify_codex_available() -> Result<PathBuf> {
    let binary = codex_binary()?;
    let output = codex_command(&binary)
        .arg("--version")
        .output()
        .with_context(|| format!("run {} --version", binary.display()))?;
    if !output.status.success() {
        anyhow::bail!(
            "Codex CLI version check failed for {}: {}",
            binary.display(),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(binary)
}

fn codex_binary() -> Result<PathBuf> {
    if let Some(explicit) = std::env::var_os("CODEX_SYNC_CODEX_BIN") {
        return resolve_explicit_codex_binary(PathBuf::from(explicit));
    }
    resolve_default_codex_binary()
}

#[cfg(not(windows))]
fn resolve_explicit_codex_binary(path: PathBuf) -> Result<PathBuf> {
    Ok(path)
}

#[cfg(not(windows))]
fn resolve_default_codex_binary() -> Result<PathBuf> {
    Ok(PathBuf::from("codex"))
}

#[cfg(windows)]
fn resolve_explicit_codex_binary(path: PathBuf) -> Result<PathBuf> {
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase);
    if !matches!(extension.as_deref(), Some("exe" | "cmd" | "bat")) {
        anyhow::bail!(
            "CODEX_SYNC_CODEX_BIN must point to codex.exe, codex.cmd, or codex.bat on Windows; got {}",
            path.display()
        );
    }
    if !path.is_file() {
        anyhow::bail!(
            "CODEX_SYNC_CODEX_BIN does not point to a file: {}",
            path.display()
        );
    }
    Ok(path)
}

#[cfg(windows)]
fn resolve_default_codex_binary() -> Result<PathBuf> {
    let path = std::env::var_os("PATH").context("PATH is not set")?;
    let directories: Vec<PathBuf> = std::env::split_paths(&path)
        .filter(|directory| !directory.as_os_str().is_empty())
        .collect();
    for filename in ["codex.exe", "codex.cmd", "codex.bat"] {
        for directory in &directories {
            let candidate = directory.join(filename);
            if candidate.is_file() && codex_candidate_works(&candidate) {
                return Ok(candidate);
            }
        }
    }
    anyhow::bail!(
        "Codex CLI is not executable from PATH; checked codex.exe, codex.cmd, and codex.bat. Set CODEX_SYNC_CODEX_BIN to the full path of a working launcher"
    )
}

#[cfg(windows)]
fn codex_candidate_works(path: &Path) -> bool {
    codex_command(path)
        .arg("--version")
        .output()
        .is_ok_and(|output| output.status.success())
}

fn codex_command(binary: &Path) -> Command {
    let mut command = Command::new(binary);
    command.env_remove("CODEX_SYNC_GITHUB_TOKEN");
    if let Some(directory) = stable_codex_working_directory() {
        command.current_dir(directory);
    }
    command
}

fn stable_codex_working_directory() -> Option<PathBuf> {
    let configured = std::env::var_os("CODEX_HOME").map(PathBuf::from);
    let home = if cfg!(windows) {
        std::env::var_os("USERPROFILE").map(PathBuf::from)
    } else {
        std::env::var_os("HOME").map(PathBuf::from)
    };
    configured
        .or_else(|| home.map(|path| path.join(".codex")))
        .filter(|path| path.is_dir())
        .or_else(|| {
            let temporary = std::env::temp_dir();
            temporary.is_dir().then_some(temporary)
        })
}

fn codex_output(arguments: &[&str]) -> Result<Output> {
    let binary = codex_binary()?;
    let output = codex_command(&binary)
        .args(arguments)
        .output()
        .with_context(|| format!("run {} {}", binary.display(), arguments.join(" ")))?;
    if !output.status.success() {
        anyhow::bail!(
            "{} {} failed: {}",
            binary.display(),
            arguments.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(output)
}

fn codex_owned(arguments: &[String]) -> Result<Output> {
    let borrowed: Vec<&str> = arguments.iter().map(String::as_str).collect();
    codex_output(&borrowed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(windows)]
    use std::sync::Mutex;

    #[cfg(windows)]
    static ENVIRONMENT_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn plugin_ids_require_marketplace() {
        assert!(validate_plugin_id("plugin@marketplace").is_ok());
        assert!(validate_plugin_id("plugin").is_err());
        assert!(validate_plugin_id("plugin@../bad").is_err());
    }

    #[test]
    fn openai_marketplaces_are_detected_by_namespace() {
        assert!(is_openai_managed_marketplace("openai-bundled"));
        assert!(is_openai_managed_marketplace("openai-primary-runtime"));
        assert!(is_openai_managed_marketplace("openai-curated-remote"));
        assert!(!is_openai_managed_marketplace("dale0525-codex-plugins"));
        assert!(!is_openai_managed_marketplace("personal"));
    }

    #[test]
    fn plugin_removals_are_scoped_to_declared_non_openai_marketplaces() {
        let installed = [
            InstalledPlugin {
                plugin_id: "retired@managed-market".to_owned(),
                installed: true,
                enabled: true,
            },
            InstalledPlugin {
                plugin_id: "current@managed-market".to_owned(),
                installed: true,
                enabled: true,
            },
            InstalledPlugin {
                plugin_id: "local@personal".to_owned(),
                installed: true,
                enabled: true,
            },
            InstalledPlugin {
                plugin_id: "browser@openai-bundled".to_owned(),
                installed: true,
                enabled: true,
            },
        ];
        let specs = [PluginSpec {
            id: "current@managed-market".to_owned(),
            enabled: true,
            auto_provision: false,
        }];
        let managed_marketplaces =
            BTreeSet::from(["managed-market".to_owned(), "openai-bundled".to_owned()]);

        assert_eq!(
            plugin_ids_to_remove(&installed, &specs, &managed_marketplaces).unwrap(),
            ["retired@managed-market"]
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_resolver_skips_broken_exe_and_uses_cmd_wrapper() {
        let _guard = ENVIRONMENT_LOCK.lock().unwrap();
        let temporary = tempfile::tempdir().unwrap();
        let broken = temporary.path().join("broken");
        let working = temporary.path().join("working");
        fs::create_dir_all(&broken).unwrap();
        fs::create_dir_all(&working).unwrap();
        fs::write(broken.join("codex.exe"), b"not a Windows executable").unwrap();
        fs::write(
            working.join("codex.cmd"),
            b"@echo off\r\nif \"%~1\"==\"--version\" exit /b 0\r\nexit /b 1\r\n",
        )
        .unwrap();
        let original = std::env::var_os("PATH");
        let joined = std::env::join_paths([broken.as_path(), working.as_path()]).unwrap();
        std::env::set_var("PATH", joined);

        let resolved = resolve_default_codex_binary().unwrap();

        if let Some(value) = original {
            std::env::set_var("PATH", value);
        } else {
            std::env::remove_var("PATH");
        }
        assert_eq!(resolved, working.join("codex.cmd"));
    }

    #[cfg(windows)]
    #[test]
    fn windows_explicit_override_rejects_extensionless_wrapper() {
        let temporary = tempfile::tempdir().unwrap();
        let wrapper = temporary.path().join("codex");
        fs::write(&wrapper, b"wrapper").unwrap();

        let error = resolve_explicit_codex_binary(wrapper).unwrap_err();

        assert!(error
            .to_string()
            .contains("must point to codex.exe, codex.cmd, or codex.bat"));
    }
}
