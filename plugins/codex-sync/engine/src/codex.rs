use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use anyhow::{Context, Result};
use serde::Deserialize;

use crate::model::{portable_name, Marketplace};
use crate::storage::atomic_write;

#[derive(Debug, Clone, Deserialize)]
pub struct InstalledPlugin {
    #[serde(alias = "pluginId", alias = "id")]
    pub plugin_id: String,
    #[serde(default = "true_value")]
    pub installed: bool,
    #[serde(default = "true_value")]
    pub enabled: bool,
}

fn true_value() -> bool {
    true
}

#[derive(Debug, Deserialize, Default)]
struct PluginList {
    #[serde(default, alias = "plugins")]
    installed: Vec<InstalledPlugin>,
}

#[derive(Debug, Clone)]
pub struct ConfiguredMarket {
    pub name: String,
    pub url: Option<String>,
    pub git_ref: String,
    pub sparse: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct MarketList {
    #[serde(default)]
    marketplaces: Vec<MarketJson>,
}

#[derive(Debug, Deserialize)]
struct MarketJson {
    name: String,
    #[serde(rename = "marketplaceSource", alias = "source")]
    source: Option<MarketSource>,
    #[serde(default)]
    sparse: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct MarketSource {
    #[serde(rename = "sourceType", alias = "type", default)]
    #[allow(dead_code)]
    source_type: String,
    #[serde(default, alias = "url")]
    source: Option<String>,
    #[serde(rename = "ref", alias = "gitRef", default)]
    git_ref: Option<String>,
    #[serde(default)]
    sparse: Option<Vec<String>>,
}

pub fn binary() -> Result<PathBuf> {
    if let Some(value) = std::env::var_os("CODEX_SYNC_CODEX_BIN") {
        let path = PathBuf::from(value);
        if !path.is_file() {
            anyhow::bail!(
                "CODEX_SYNC_CODEX_BIN does not point to a file: {}",
                path.display()
            );
        }
        return Ok(path);
    }
    Ok(PathBuf::from("codex"))
}

fn command(arguments: &[String]) -> Result<Output> {
    let binary = binary()?;
    let mut command = Command::new(&binary);
    command.args(arguments);
    command.env_remove("CODEX_SYNC_GITHUB_TOKEN");
    if let Some(codex_home) = std::env::var_os("CODEX_HOME") {
        command.current_dir(codex_home);
    }
    let output = command
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

pub fn installed_plugins() -> Result<Vec<InstalledPlugin>> {
    let output = command(&["plugin".to_owned(), "list".to_owned(), "--json".to_owned()])?;
    if let Ok(list) = serde_json::from_slice::<PluginList>(&output.stdout) {
        return Ok(list.installed);
    }
    serde_json::from_slice::<Vec<InstalledPlugin>>(&output.stdout)
        .context("parse Codex plugin list")
}

pub fn configured_markets() -> Result<Vec<ConfiguredMarket>> {
    let output = command(&[
        "plugin".to_owned(),
        "marketplace".to_owned(),
        "list".to_owned(),
        "--json".to_owned(),
    ])?;
    if let Ok(list) = serde_json::from_slice::<MarketList>(&output.stdout) {
        return Ok(list
            .marketplaces
            .into_iter()
            .map(|market| {
                let source = market.source;
                ConfiguredMarket {
                    name: market.name,
                    url: source
                        .as_ref()
                        .filter(|item| item.source_type.is_empty() || item.source_type == "git")
                        .and_then(|item| item.source.clone()),
                    git_ref: source
                        .as_ref()
                        .and_then(|item| item.git_ref.clone())
                        .unwrap_or_else(|| "main".to_owned()),
                    sparse: source.and_then(|item| item.sparse).or(market.sparse),
                }
            })
            .collect());
    }
    parse_market_text(&String::from_utf8_lossy(&output.stdout))
}

fn parse_market_text(text: &str) -> Result<Vec<ConfiguredMarket>> {
    let mut result = Vec::new();
    for line in text.lines().skip(1) {
        let mut parts = line.split_whitespace();
        let Some(name) = parts.next() else { continue };
        let Some(url) = parts.next() else { continue };
        if portable_name(name) {
            result.push(ConfiguredMarket {
                name: name.to_owned(),
                url: (!url.is_empty()).then(|| url.to_owned()),
                git_ref: "main".to_owned(),
                sparse: None,
            });
        }
    }
    Ok(result)
}

pub fn add_or_update_market(market: &Marketplace) -> Result<()> {
    let mut args = vec![
        "plugin".to_owned(),
        "marketplace".to_owned(),
        "add".to_owned(),
        market.url.clone(),
        "--ref".to_owned(),
        market.git_ref.clone(),
    ];
    for sparse in &market.sparse {
        args.push("--sparse".to_owned());
        args.push(sparse.clone());
    }
    command(&args)?;
    let _ = command(&[
        "plugin".to_owned(),
        "marketplace".to_owned(),
        "upgrade".to_owned(),
        market.name.clone(),
    ])?;
    Ok(())
}

pub fn remove_market(name: &str) -> Result<()> {
    command(&[
        "plugin".to_owned(),
        "marketplace".to_owned(),
        "remove".to_owned(),
        name.to_owned(),
    ])?;
    Ok(())
}

pub fn install_plugin(id: &str) -> Result<()> {
    command(&["plugin".to_owned(), "add".to_owned(), id.to_owned()])?;
    Ok(())
}

pub fn remove_plugin(id: &str) -> Result<()> {
    command(&["plugin".to_owned(), "remove".to_owned(), id.to_owned()])?;
    Ok(())
}

pub fn reconcile(
    desired_markets: &[Marketplace],
    desired_plugins: &BTreeSet<String>,
    previously_managed: &BTreeSet<String>,
    dry_run: bool,
) -> Result<ConvergenceReport> {
    let configured = configured_markets()?;
    let configured_by_name: BTreeMap<_, _> = configured
        .iter()
        .map(|market| (market.name.as_str(), market))
        .collect();
    let desired_names: BTreeSet<_> = desired_markets
        .iter()
        .map(|market| market.name.as_str())
        .collect();
    let installed = installed_plugins()?;
    let installed_ids: BTreeMap<_, _> = installed
        .iter()
        .filter(|plugin| plugin.installed)
        .map(|plugin| (plugin.plugin_id.as_str(), plugin))
        .collect();
    let mut report = ConvergenceReport::default();
    let mut removed_plugins = BTreeSet::new();
    let mut affected_markets = BTreeSet::new();
    let mut conflicted_markets = BTreeSet::new();
    for market in desired_markets {
        if let Some(current) = configured_by_name.get(market.name.as_str()) {
            let source_changed = current.url.as_deref() != Some(market.url.as_str())
                || current.git_ref != market.git_ref
                || current.sparse.as_deref().unwrap_or(&[]) != market.sparse.as_slice();
            if source_changed {
                if previously_managed.contains(&market.name) {
                    affected_markets.insert(market.name.clone());
                } else {
                    conflicted_markets.insert(market.name.clone());
                }
            }
        }
    }
    for name in previously_managed {
        if !desired_names.contains(name.as_str()) && !is_openai_market(name) {
            affected_markets.insert(name.clone());
        }
    }
    // Detach plugins before taking down an affected marketplace. This keeps
    // replacement/removal safe and makes retry order deterministic.
    for plugin in installed.iter().filter(|plugin| plugin.installed) {
        let market = plugin_marketplace(&plugin.plugin_id)?;
        if affected_markets.contains(market) && !is_openai_market(market) {
            report
                .actions
                .push(format!("remove plugin {}", plugin.plugin_id));
            removed_plugins.insert(plugin.plugin_id.clone());
            if !dry_run {
                remove_plugin(&plugin.plugin_id)?;
            }
        }
    }
    for name in &affected_markets {
        if !is_openai_market(name) {
            report.actions.push(format!("remove marketplace {name}"));
            if !dry_run {
                remove_market(name)?;
            }
        }
    }
    for market in desired_markets {
        market.validate()?;
        if conflicted_markets.contains(&market.name) {
            report
                .actions
                .push(format!("preserve unmanaged marketplace {}", market.name));
            continue;
        }
        let source_changed = configured_by_name
            .get(market.name.as_str())
            .is_some_and(|current| {
                current.url.as_deref() != Some(market.url.as_str())
                    || current.git_ref != market.git_ref
                    || current.sparse.as_deref().unwrap_or(&[]) != market.sparse.as_slice()
            });
        if !configured_by_name.contains_key(market.name.as_str()) || source_changed {
            report
                .actions
                .push(format!("register marketplace {}", market.name));
            if !dry_run {
                add_or_update_market(market)?;
            }
        } else {
            report
                .actions
                .push(format!("refresh marketplace {}", market.name));
            if !dry_run {
                let _ = command(&[
                    "plugin".to_owned(),
                    "marketplace".to_owned(),
                    "upgrade".to_owned(),
                    market.name.clone(),
                ])?;
            }
        }
        if !is_openai_market(&market.name) {
            report.managed_markets.insert(market.name.clone());
        }
    }
    for id in desired_plugins {
        validate_plugin_id(id)?;
        let market = plugin_marketplace(id)?;
        if conflicted_markets.contains(market) {
            report.actions.push(format!(
                "skip plugin {id} for preserved marketplace {market}"
            ));
            continue;
        }
        if removed_plugins.contains(id)
            || !installed_ids
                .get(id.as_str())
                .is_some_and(|plugin| plugin.enabled)
        {
            report.actions.push(format!("install plugin {id}"));
            if !dry_run {
                install_plugin(id)?;
            }
        }
    }
    for plugin in installed.iter().filter(|plugin| plugin.installed) {
        let market = plugin_marketplace(&plugin.plugin_id)?;
        if previously_managed.contains(market)
            && !affected_markets.contains(market)
            && !desired_plugins.contains(&plugin.plugin_id)
            && !is_openai_market(market)
        {
            report
                .actions
                .push(format!("remove plugin {}", plugin.plugin_id));
            if !dry_run {
                remove_plugin(&plugin.plugin_id)?;
            }
        }
    }
    Ok(report)
}

#[derive(Debug, Default)]
pub struct ConvergenceReport {
    pub actions: Vec<String>,
    pub managed_markets: BTreeSet<String>,
}

pub fn validate_plugin_id(value: &str) -> Result<()> {
    let Some((plugin, market)) = value.split_once('@') else {
        anyhow::bail!("plugin id must use plugin@marketplace syntax: {value}");
    };
    if !portable_name(plugin) || !portable_name(market) {
        anyhow::bail!("invalid plugin id: {value}");
    }
    Ok(())
}

pub fn plugin_marketplace(value: &str) -> Result<&str> {
    validate_plugin_id(value)?;
    Ok(value.split_once('@').expect("validated plugin id").1)
}

pub fn is_openai_market(value: &str) -> bool {
    value == "openai" || value.starts_with("openai-")
}

pub fn portable_git_markets_with_metadata() -> Result<Vec<ConfiguredMarket>> {
    let configured = configured_markets()?;
    let mut result = Vec::new();
    for market in configured {
        if is_openai_market(&market.name) {
            continue;
        }
        let Some(url) = market.url.as_ref() else {
            continue;
        };
        if !(url.starts_with("https://") || url.starts_with("git@") || url.starts_with("ssh://"))
            || url.starts_with("https://") && url.contains('@')
        {
            continue;
        }
        result.push(market);
    }
    Ok(result)
}

pub fn current_plugins() -> Result<BTreeSet<String>> {
    let mut result = BTreeSet::new();
    for plugin in installed_plugins()?
        .into_iter()
        .filter(|item| item.installed && item.enabled)
    {
        let market = plugin_marketplace(&plugin.plugin_id)?;
        if !is_openai_market(market) {
            result.insert(plugin.plugin_id);
        }
    }
    Ok(result)
}

pub fn write_markets(path: &Path, markets: &[Marketplace]) -> Result<()> {
    let entries = markets
        .iter()
        .map(|market| {
            let mut table = toml::map::Map::new();
            table.insert("source".to_owned(), toml::Value::String("git".to_owned()));
            table.insert("name".to_owned(), toml::Value::String(market.name.clone()));
            table.insert("url".to_owned(), toml::Value::String(market.url.clone()));
            table.insert(
                "git_ref".to_owned(),
                toml::Value::String(market.git_ref.clone()),
            );
            table.insert(
                "sparse".to_owned(),
                toml::Value::Array(
                    market
                        .sparse
                        .iter()
                        .cloned()
                        .map(toml::Value::String)
                        .collect(),
                ),
            );
            toml::Value::Table(table)
        })
        .collect();
    let mut root = toml::map::Map::new();
    root.insert("marketplaces".to_owned(), toml::Value::Array(entries));
    atomic_write(
        path,
        toml::to_string_pretty(&toml::Value::Table(root))?.as_bytes(),
    )
}

pub fn write_plugins(path: &Path, plugins: &BTreeSet<String>) -> Result<()> {
    let mut root = toml::map::Map::new();
    root.insert(
        "plugins".to_owned(),
        toml::Value::Array(plugins.iter().cloned().map(toml::Value::String).collect()),
    );
    atomic_write(
        path,
        toml::to_string_pretty(&toml::Value::Table(root))?.as_bytes(),
    )
}
