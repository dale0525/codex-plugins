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
    let mut messages = Vec::new();
    for spec in specs {
        match spec {
            MarketplaceSpec::Git {
                name,
                url,
                git_ref,
                sparse,
            } => {
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

fn codex_binary() -> PathBuf {
    std::env::var_os("CODEX_SYNC_CODEX_BIN")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("codex"))
}

fn codex_output(arguments: &[&str]) -> Result<Output> {
    let output = Command::new(codex_binary())
        .args(arguments)
        .env_remove("CODEX_SYNC_GITHUB_TOKEN")
        .output()
        .with_context(|| format!("run codex {}", arguments.join(" ")))?;
    if !output.status.success() {
        anyhow::bail!(
            "codex {} failed: {}",
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
        }];
        let managed_marketplaces =
            BTreeSet::from(["managed-market".to_owned(), "openai-bundled".to_owned()]);

        assert_eq!(
            plugin_ids_to_remove(&installed, &specs, &managed_marketplaces).unwrap(),
            ["retired@managed-market"]
        );
    }
}
