use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result};
use serde::Deserialize;

use crate::model::PluginSpec;
use crate::reconcile::{marketplace_roots, plugin_marketplace};

#[derive(Debug, Deserialize)]
struct MarketplaceIndex {
    #[serde(default)]
    plugins: Vec<MarketplacePlugin>,
}

#[derive(Debug, Deserialize)]
struct MarketplacePlugin {
    name: String,
    source: MarketplacePluginSource,
}

#[derive(Debug, Deserialize)]
struct MarketplacePluginSource {
    source: String,
    path: String,
}

#[derive(Debug, Deserialize)]
struct ProvisionSpec {
    schema_version: u32,
    risk: String,
    posix_script: String,
    windows_script: String,
    #[serde(default)]
    arguments: Vec<String>,
}

fn provision_launcher(windows: bool) -> (&'static str, &'static [&'static str]) {
    if windows {
        (
            "pwsh",
            &[
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ],
        )
    } else {
        ("/bin/sh", &[])
    }
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

fn resolve_plugin_root(plugin_id: &str) -> Result<PathBuf> {
    let (plugin_name, _) = plugin_id
        .split_once('@')
        .context("validated plugin ID has no marketplace separator")?;
    let marketplace_name = plugin_marketplace(plugin_id)?;
    let roots = marketplace_roots()?;
    let marketplace_root = roots
        .get(marketplace_name)
        .with_context(|| format!("marketplace {marketplace_name} is not registered"))?;
    let index_path = marketplace_root.join(".agents/plugins/marketplace.json");
    let index: MarketplaceIndex = serde_json::from_slice(
        &fs::read(&index_path).with_context(|| format!("read {}", index_path.display()))?,
    )
    .with_context(|| format!("parse {}", index_path.display()))?;
    let entry = index
        .plugins
        .into_iter()
        .find(|entry| entry.name == plugin_name)
        .with_context(|| format!("plugin {plugin_name} is absent from marketplace index"))?;
    if entry.source.source != "local" {
        anyhow::bail!("auto provisioning requires a local plugin source entry")
    }
    safe_child(marketplace_root, entry.source.path.trim_start_matches("./"))
}

fn run_one(plugin: &PluginSpec) -> Result<String> {
    let plugin_root = resolve_plugin_root(&plugin.id)?;
    let specification_path = safe_child(&plugin_root, ".codex-sync/provision.json")?;
    let specification: ProvisionSpec = serde_json::from_slice(
        &fs::read(&specification_path)
            .with_context(|| format!("read {}", specification_path.display()))?,
    )
    .with_context(|| format!("parse {}", specification_path.display()))?;
    if specification.schema_version != 1 || specification.risk != "high" {
        anyhow::bail!("plugin provision specification must declare schema 1 and high risk")
    }
    let script_value = if cfg!(windows) {
        &specification.windows_script
    } else {
        &specification.posix_script
    };
    let script = safe_child(&plugin_root, script_value.trim_start_matches("./"))?;
    if !script.is_file() {
        anyhow::bail!(
            "plugin provision script is not a file: {}",
            script.display()
        )
    }
    let (launcher, launcher_arguments) = provision_launcher(cfg!(windows));
    let mut command = Command::new(launcher);
    command.args(launcher_arguments);
    let output = command
        .arg(&script)
        .args(&specification.arguments)
        .current_dir(&plugin_root)
        .env("PLUGIN_ROOT", &plugin_root)
        .env_remove("CODEX_SYNC_GITHUB_TOKEN")
        .env_remove("GITHUB_TOKEN")
        .env_remove("GH_TOKEN")
        .output()
        .with_context(|| format!("run provisioner for {}", plugin.id))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("provisioner for {} failed: {}", plugin.id, detail.trim());
    }
    let detail = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    Ok(if detail.is_empty() {
        format!("provisioned {}", plugin.id)
    } else {
        format!("provisioned {}: {detail}", plugin.id)
    })
}

pub fn run_auto_provisioners(plugins: &[PluginSpec]) -> Result<Vec<String>> {
    plugins
        .iter()
        .filter(|plugin| plugin.enabled && plugin.auto_provision)
        .map(run_one)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_child_rejects_parent_traversal() {
        let temporary = tempfile::tempdir().unwrap();
        assert!(safe_child(temporary.path(), "../outside").is_err());
    }

    #[test]
    fn windows_provisioners_bypass_only_process_execution_policy() {
        let (launcher, arguments) = provision_launcher(true);
        assert_eq!(launcher, "pwsh");
        assert_eq!(
            arguments,
            [
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ]
        );
    }
}
