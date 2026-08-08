use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::Value as JsonValue;

use crate::model::{portable_name, Marketplace};
use crate::storage::{atomic_write, git_text, git_try};

#[derive(Debug, Clone, Deserialize)]
pub struct InstalledPlugin {
    #[serde(alias = "pluginId", alias = "id")]
    pub plugin_id: String,
    #[serde(rename = "marketplaceName", default)]
    pub marketplace_name: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    pub version: Option<String>,
    #[serde(default)]
    pub source: Option<JsonValue>,
    #[serde(rename = "marketplaceSource", default)]
    #[allow(dead_code)]
    pub marketplace_source: Option<JsonValue>,
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
    pub source_type: String,
    pub root: Option<PathBuf>,
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
    #[serde(default)]
    root: Option<PathBuf>,
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
    let codex_home = std::env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .or_else(|| directories::BaseDirs::new().map(|dirs| dirs.home_dir().join(".codex")))
        .context("CODEX_HOME is not available")?;
    let appserver = codex_home.join("plugins").join(".plugin-appserver");
    for name in ["codex", "codex.exe", "codex.cmd", "codex.bat"] {
        let path = appserver.join(name);
        if path.is_file() {
            return Ok(path);
        }
    }
    anyhow::bail!(
        "Codex plugin appserver executable was not found under {}; set CODEX_SYNC_CODEX_BIN to a reviewed executable",
        appserver.display()
    )
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
                let source_type = source
                    .as_ref()
                    .map(|item| item.source_type.clone())
                    .unwrap_or_default();
                let root = market.root.or_else(|| {
                    source
                        .as_ref()
                        .filter(|item| item.source_type == "local")
                        .and_then(|item| item.source.as_ref())
                        .map(PathBuf::from)
                });
                ConfiguredMarket {
                    name: market.name,
                    url: source.as_ref().and_then(|item| item.source.clone()),
                    git_ref: source
                        .as_ref()
                        .and_then(|item| item.git_ref.clone())
                        .unwrap_or_else(|| "main".to_owned()),
                    sparse: source.and_then(|item| item.sparse).or(market.sparse),
                    source_type,
                    root,
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
                source_type: "git".to_owned(),
                root: None,
            });
        }
    }
    Ok(result)
}

pub fn add_or_update_market(market: &Marketplace) -> Result<()> {
    if is_protected_market(&market.name) {
        anyhow::bail!("protected marketplaces are not managed");
    }
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
    Ok(())
}

pub fn refresh_market(name: &str) -> Result<()> {
    if is_protected_market(name) {
        anyhow::bail!("protected marketplaces are not managed");
    }
    let _ = command(&[
        "plugin".to_owned(),
        "marketplace".to_owned(),
        "upgrade".to_owned(),
        name.to_owned(),
    ])?;
    Ok(())
}

pub fn remove_market(name: &str) -> Result<()> {
    if is_protected_market(name) {
        anyhow::bail!("protected marketplaces are not managed");
    }
    command(&[
        "plugin".to_owned(),
        "marketplace".to_owned(),
        "remove".to_owned(),
        name.to_owned(),
    ])?;
    Ok(())
}

pub fn install_plugin(id: &str) -> Result<()> {
    if id
        .split_once('@')
        .is_some_and(|(_, market)| is_protected_market(market))
    {
        anyhow::bail!("protected plugins are not managed");
    }
    command(&["plugin".to_owned(), "add".to_owned(), id.to_owned()])?;
    Ok(())
}

pub fn remove_plugin(id: &str) -> Result<()> {
    if id
        .split_once('@')
        .is_some_and(|(_, market)| is_protected_market(market))
    {
        anyhow::bail!("protected plugins are not managed");
    }
    command(&["plugin".to_owned(), "remove".to_owned(), id.to_owned()])?;
    Ok(())
}

fn plugin_market_name(plugin: &InstalledPlugin) -> Result<String> {
    let id_market = plugin_marketplace(&plugin.plugin_id)?.to_owned();
    if let Some(name) = plugin.marketplace_name.as_deref() {
        if !portable_name(name) {
            anyhow::bail!("invalid plugin marketplace name");
        }
        if name != id_market {
            anyhow::bail!("plugin marketplace metadata does not match plugin id");
        }
        return Ok(name.to_owned());
    }
    Ok(id_market)
}

fn plugin_is_protected(plugin: &InstalledPlugin) -> Result<bool> {
    Ok(is_protected_market(plugin_marketplace(&plugin.plugin_id)?)
        || is_protected_market(&plugin_market_name(plugin)?))
}

fn raw_plugin_is_protected(plugin: &InstalledPlugin) -> bool {
    plugin
        .marketplace_name
        .as_deref()
        .is_some_and(is_protected_market)
        || plugin
            .plugin_id
            .split_once('@')
            .is_some_and(|(_, market)| is_protected_market(market))
}

pub fn reconcile(
    desired_markets: &[Marketplace],
    desired_plugins: &BTreeSet<String>,
    dry_run: bool,
) -> Result<ConvergenceReport> {
    let desired_by_name = desired_markets_by_name(desired_markets)?;
    let desired_plugins = desired_plugins_for_markets(desired_plugins, &desired_by_name)?;
    let configured = configured_markets()?;
    let installed = installed_plugins()?;
    let (portable_markets, nonportable_names) = local_market_sets(&configured)?;
    for market in desired_by_name.values() {
        if nonportable_names.contains(&market.name) {
            anyhow::bail!(
                "desired marketplace {} conflicts with a non-portable local marketplace",
                market.name
            );
        }
    }
    let local_plugins = local_plugins(&installed, &portable_markets)?;
    let mut report = ConvergenceReport::default();
    let desired_names = desired_by_name.keys().cloned().collect::<BTreeSet<_>>();
    let mut affected_markets = BTreeSet::new();
    for (name, desired) in &desired_by_name {
        if let Some(current) = portable_markets.get(name) {
            if !same_identity(current, desired) {
                affected_markets.insert(name.clone());
            }
        }
    }
    for name in portable_markets.keys() {
        if !desired_names.contains(name) {
            affected_markets.insert(name.clone());
        }
    }

    // Detach plugins from source-mismatched and removed marketplaces first.
    let mut removed_plugins = BTreeSet::new();
    for (id, (_, market)) in &local_plugins {
        if affected_markets.contains(market) {
            report.actions.push(format!("remove plugin {id}"));
            removed_plugins.insert(id.clone());
            if !dry_run {
                remove_plugin(id)?;
            }
        }
    }
    // Then remove installed plugins outside the remote desired set.
    for (id, (_, _market)) in &local_plugins {
        if !desired_plugins.contains(id) && !removed_plugins.contains(id) {
            report.actions.push(format!("remove plugin {id}"));
            if !dry_run {
                remove_plugin(id)?;
            }
        }
    }
    for name in &affected_markets {
        if portable_markets.contains_key(name) {
            report.actions.push(format!("remove marketplace {name}"));
            if !dry_run {
                remove_market(name)?;
            }
        }
    }
    // Add/replace desired sources, then refresh every desired source.
    for (name, market) in &desired_by_name {
        let needs_add = portable_markets
            .get(name)
            .is_none_or(|current| !same_identity(current, market));
        if needs_add {
            report.actions.push(format!("register marketplace {name}"));
            if !dry_run {
                add_or_update_market(market)?;
            }
        }
    }
    for name in desired_by_name.keys() {
        report.actions.push(format!("refresh marketplace {name}"));
        if !dry_run {
            refresh_market(name)?;
        }
    }
    for id in &desired_plugins {
        report.actions.push(format!("install plugin {id}"));
        if !dry_run {
            install_plugin(id)?;
        }
    }
    if !dry_run {
        verify_converged(&desired_by_name, &desired_plugins)?;
    }
    Ok(report)
}

#[derive(Debug, Default)]
pub struct ConvergenceReport {
    pub actions: Vec<String>,
}

fn desired_markets_by_name(desired: &[Marketplace]) -> Result<BTreeMap<String, Marketplace>> {
    let mut result = BTreeMap::new();
    for market in desired {
        if is_protected_market(&market.name) {
            continue;
        }
        market.validate()?;
        if result.insert(market.name.clone(), market.clone()).is_some() {
            anyhow::bail!("duplicate desired marketplace {}", market.name);
        }
    }
    Ok(result)
}

fn desired_plugins_for_markets(
    desired: &BTreeSet<String>,
    markets: &BTreeMap<String, Marketplace>,
) -> Result<BTreeSet<String>> {
    let mut result = BTreeSet::new();
    for id in desired {
        validate_plugin_id(id)?;
        let market = plugin_marketplace(id)?;
        if is_protected_market(market) {
            continue;
        }
        if !markets.contains_key(market) {
            anyhow::bail!("plugin {id} references missing remote marketplace {market}");
        }
        result.insert(id.clone());
    }
    Ok(result)
}

fn local_market_sets(
    configured: &[ConfiguredMarket],
) -> Result<(BTreeMap<String, Marketplace>, BTreeSet<String>)> {
    let mut portable = BTreeMap::new();
    let mut nonportable = BTreeSet::new();
    for market in configured {
        if is_protected_market(&market.name) {
            continue;
        }
        if let Some(portable_market) = portable_market(market)? {
            if portable
                .insert(market.name.clone(), portable_market)
                .is_some()
            {
                anyhow::bail!("duplicate local marketplace {}", market.name);
            }
        } else if !nonportable.insert(market.name.clone()) {
            anyhow::bail!("duplicate local marketplace {}", market.name);
        }
    }
    Ok((portable, nonportable))
}

fn portable_market(market: &ConfiguredMarket) -> Result<Option<Marketplace>> {
    if !market.source_type.is_empty() && market.source_type != "git" {
        return Ok(None);
    }
    let Some(url) = market.url.as_deref() else {
        return Ok(None);
    };
    if !safe_git_origin(url) {
        return Ok(None);
    }
    let sparse = market.sparse.clone().unwrap_or_default();
    Ok(Some(Marketplace {
        name: market.name.clone(),
        url: url.to_owned(),
        git_ref: market.git_ref.clone(),
        sparse,
    }))
}

fn same_identity(current: &Marketplace, desired: &Marketplace) -> bool {
    current.name == desired.name
        && current.url == desired.url
        && current.git_ref == desired.git_ref
        && current.sparse == desired.sparse
}

fn local_plugins(
    installed: &[InstalledPlugin],
    portable_markets: &BTreeMap<String, Marketplace>,
) -> Result<BTreeMap<String, (InstalledPlugin, String)>> {
    let mut result = BTreeMap::new();
    for plugin in installed.iter().filter(|plugin| plugin.installed) {
        if raw_plugin_is_protected(plugin) {
            continue;
        }
        let market = plugin_market_name(plugin)?;
        if !plugin_is_protected(plugin)?
            && portable_markets.contains_key(&market)
            && result
                .insert(plugin.plugin_id.clone(), (plugin.clone(), market))
                .is_some()
        {
            anyhow::bail!("duplicate installed plugin {}", plugin.plugin_id);
        }
    }
    Ok(result)
}

fn verify_converged(
    desired_markets: &BTreeMap<String, Marketplace>,
    desired_plugins: &BTreeSet<String>,
) -> Result<()> {
    let configured = configured_markets()?;
    let (portable, _) = local_market_sets(&configured)?;
    if portable.len() != desired_markets.len()
        || portable.iter().any(|(name, market)| {
            desired_markets
                .get(name)
                .is_none_or(|wanted| !same_identity(market, wanted))
        })
    {
        anyhow::bail!("marketplace set did not converge");
    }
    let installed = installed_plugins()?;
    let local = local_plugins(&installed, &portable)?;
    let actual = local.keys().cloned().collect::<BTreeSet<_>>();
    if actual != *desired_plugins {
        anyhow::bail!("plugin set did not converge");
    }
    for id in desired_plugins {
        let plugin = installed
            .iter()
            .find(|plugin| plugin.plugin_id == *id && plugin.installed)
            .with_context(|| format!("plugin {id} is not installed"))?;
        if !plugin.enabled {
            anyhow::bail!("plugin {id} did not converge to enabled");
        }
    }
    Ok(())
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

pub fn is_protected_market(value: &str) -> bool {
    value == "personal" || is_openai_market(value)
}

pub fn validate_git_origin(value: &str) -> Result<()> {
    if value.is_empty()
        || value
            .chars()
            .any(|ch| ch.is_control() || ch.is_whitespace())
    {
        anyhow::bail!("Git origin is empty or contains whitespace");
    }
    if value.starts_with("https://") {
        let authority = value
            .strip_prefix("https://")
            .and_then(|rest| rest.split(['/', '?', '#']).next())
            .unwrap_or_default();
        if authority.is_empty() || authority.contains('@') {
            anyhow::bail!("Git origin HTTPS URL is invalid");
        }
        return Ok(());
    }
    if value.starts_with("ssh://") {
        let authority = value
            .strip_prefix("ssh://")
            .and_then(|rest| rest.split(['/', '?', '#']).next())
            .unwrap_or_default();
        if authority.is_empty() {
            anyhow::bail!("Git origin SSH URL is invalid");
        }
        if let Some(at) = authority.rfind('@') {
            if authority[..at].contains(':') {
                anyhow::bail!("Git origin SSH URL contains a password");
            }
        }
        return Ok(());
    }
    if let Some((user, rest)) = value.split_once('@') {
        if !user.is_empty()
            && !user.contains(':')
            && rest
                .split_once(':')
                .is_some_and(|(_, path)| !path.is_empty())
        {
            return Ok(());
        }
    }
    anyhow::bail!("Git origin must use HTTPS, SSH, or scp syntax")
}

fn safe_git_origin(value: &str) -> bool {
    validate_git_origin(value).is_ok()
}

#[derive(Debug, Default)]
pub struct CaptureInventory {
    pub markets: Vec<Marketplace>,
    pub plugins: BTreeSet<String>,
    pub warnings: Vec<String>,
}

pub fn capture_inventory(previous_markets: &[Marketplace]) -> Result<CaptureInventory> {
    let configured = configured_markets()?;
    let mut by_market: BTreeMap<String, Vec<InstalledPlugin>> = BTreeMap::new();
    for plugin in installed_plugins()?
        .into_iter()
        .filter(|item| item.installed)
    {
        if raw_plugin_is_protected(&plugin) {
            continue;
        }
        validate_plugin_id(&plugin.plugin_id)?;
        let market = plugin_market_name(&plugin)?;
        if !plugin_is_protected(&plugin)? {
            by_market.entry(market).or_default().push(plugin);
        }
    }
    let mut candidates: BTreeMap<String, ConfiguredMarket> = configured
        .into_iter()
        .map(|market| (market.name.clone(), market))
        .collect();
    for (name, plugins) in &by_market {
        if !candidates.contains_key(name) {
            if let Some(market) = marketplace_from_plugin(plugins.first().expect("non-empty"), name)
            {
                candidates.insert(name.clone(), market);
            }
        }
    }
    let mut inventory = CaptureInventory::default();
    for configured_market in candidates.into_values() {
        if is_protected_market(&configured_market.name) {
            continue;
        }
        let Some(plugins) = by_market.get(&configured_market.name) else {
            continue;
        };
        let sparse = configured_market.sparse.clone().or_else(|| {
            previous_markets
                .iter()
                .find(|market| market.name == configured_market.name)
                .map(|market| market.sparse.clone())
        });
        match captured_market(&configured_market, plugins, sparse.unwrap_or_default()) {
            Ok(market) => {
                inventory.markets.push(market);
                inventory
                    .plugins
                    .extend(plugins.iter().map(|plugin| plugin.plugin_id.clone()));
            }
            Err(_) => inventory.warnings.push(format!(
                "skipping marketplace {}: source is not an eligible Git worktree",
                configured_market.name
            )),
        }
    }
    Ok(inventory)
}

fn marketplace_from_plugin(plugin: &InstalledPlugin, name: &str) -> Option<ConfiguredMarket> {
    let source = plugin.marketplace_source.as_ref()?;
    let source_type = source
        .get("sourceType")
        .or_else(|| source.get("type"))
        .and_then(JsonValue::as_str)
        .unwrap_or_default()
        .to_owned();
    let url = source
        .get("source")
        .or_else(|| source.get("url"))
        .and_then(JsonValue::as_str)
        .map(str::to_owned);
    let root = (source_type == "local")
        .then(|| url.as_deref().map(PathBuf::from))
        .flatten();
    Some(ConfiguredMarket {
        name: name.to_owned(),
        url,
        git_ref: source
            .get("ref")
            .or_else(|| source.get("gitRef"))
            .and_then(JsonValue::as_str)
            .unwrap_or("main")
            .to_owned(),
        sparse: source
            .get("sparse")
            .and_then(JsonValue::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(JsonValue::as_str)
                    .map(str::to_owned)
                    .collect()
            }),
        source_type,
        root,
    })
}

fn captured_market(
    configured: &ConfiguredMarket,
    plugins: &[InstalledPlugin],
    sparse: Vec<String>,
) -> Result<Marketplace> {
    let local = configured.source_type == "local"
        || (configured.source_type != "git" && !configured.source_type.is_empty())
        || (configured.source_type.is_empty()
            && configured
                .url
                .as_deref()
                .is_some_and(|url| !safe_git_origin(url)));
    if local {
        return export_local_market(configured, plugins, sparse);
    }
    let url = configured
        .url
        .as_deref()
        .context("Git marketplace URL missing")?;
    validate_git_origin(url)?;
    Ok(Marketplace {
        name: configured.name.clone(),
        url: url.to_owned(),
        git_ref: configured.git_ref.clone(),
        sparse,
    })
}

fn export_local_market(
    configured: &ConfiguredMarket,
    plugins: &[InstalledPlugin],
    sparse: Vec<String>,
) -> Result<Marketplace> {
    let source = configured
        .root
        .clone()
        .or_else(|| configured.url.as_ref().map(PathBuf::from))
        .context("local marketplace root missing")?;
    let source = fs::canonicalize(source).context("canonicalize local marketplace")?;
    let top = fs::canonicalize(git_text(&["rev-parse", "--show-toplevel"], Some(&source))?.trim())
        .context("canonicalize Git worktree root")?;
    if top != source {
        anyhow::bail!("marketplace source is not the Git worktree top level");
    }
    let origins = git_text(&["remote", "get-url", "--all", "origin"], Some(&source))?
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if origins.len() != 1 {
        anyhow::bail!("Git origin is not unique");
    }
    validate_git_origin(&origins[0])?;
    let branch = git_text(
        &["symbolic-ref", "--quiet", "--short", "HEAD"],
        Some(&source),
    )?
    .trim()
    .to_owned();
    crate::model::validate_git_ref(&branch)?;
    let (manifest_path, manifest) = read_marketplace_manifest(&source)?;
    ensure_tracked(&source, &manifest_path)?;
    let manifest_plugins = manifest_plugin_paths(&source, &manifest)?;
    for plugin in plugins {
        let plugin_root = plugin_source_root(&source, plugin, &manifest_plugins)?;
        let definition = plugin_root.join(".codex-plugin/plugin.json");
        ensure_tracked(&source, &definition)?;
    }
    Ok(Marketplace {
        name: configured.name.clone(),
        url: origins[0].clone(),
        git_ref: branch,
        sparse,
    })
}

fn read_marketplace_manifest(root: &Path) -> Result<(PathBuf, JsonValue)> {
    for relative in [
        Path::new(".agents/plugins/marketplace.json"),
        Path::new(".codex-plugin/marketplace.json"),
        Path::new("marketplace.json"),
    ] {
        let path = root.join(relative);
        if let Ok(metadata) = fs::symlink_metadata(&path) {
            if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
                anyhow::bail!("marketplace manifest is not a regular file");
            }
            let canonical = fs::canonicalize(&path).context("canonicalize marketplace manifest")?;
            if canonical != path || !canonical.starts_with(root) {
                anyhow::bail!("marketplace manifest escapes worktree");
            }
            let text = fs::read_to_string(&path).context("read marketplace manifest")?;
            let value = serde_json::from_str(&text).context("parse marketplace manifest")?;
            return Ok((path, value));
        }
    }
    anyhow::bail!("marketplace manifest is missing")
}

fn manifest_plugin_paths(root: &Path, manifest: &JsonValue) -> Result<BTreeMap<String, PathBuf>> {
    let mut result = BTreeMap::new();
    for entry in manifest
        .get("plugins")
        .and_then(JsonValue::as_array)
        .context("marketplace manifest plugins are missing")?
    {
        let Some(name) = entry.get("name").and_then(JsonValue::as_str) else {
            continue;
        };
        let Some(raw_path) = entry
            .get("source")
            .and_then(|source| source.get("path"))
            .and_then(JsonValue::as_str)
        else {
            continue;
        };
        let raw = Path::new(raw_path);
        let candidate = if raw.is_absolute() {
            raw.to_owned()
        } else {
            root.join(raw)
        };
        let path = fs::canonicalize(candidate).context("canonicalize plugin source")?;
        if !path.starts_with(root) {
            anyhow::bail!("plugin source escapes marketplace worktree");
        }
        result.insert(name.to_owned(), path);
    }
    Ok(result)
}

fn plugin_source_root(
    root: &Path,
    plugin: &InstalledPlugin,
    manifest_paths: &BTreeMap<String, PathBuf>,
) -> Result<PathBuf> {
    let plugin_name = plugin
        .plugin_id
        .split_once('@')
        .map(|(name, _)| name)
        .context("plugin id is missing name")?;
    let path = plugin
        .source
        .as_ref()
        .and_then(|source| source.get("path"))
        .and_then(JsonValue::as_str)
        .map(|raw| {
            let path = Path::new(raw);
            if path.is_absolute() {
                path.to_owned()
            } else {
                root.join(path)
            }
        })
        .or_else(|| manifest_paths.get(plugin_name).cloned())
        .context("plugin source path is missing")?;
    let path = fs::canonicalize(path).context("canonicalize plugin source")?;
    if !path.starts_with(root) {
        anyhow::bail!("plugin source escapes marketplace worktree");
    }
    Ok(path)
}

fn ensure_tracked(root: &Path, path: &Path) -> Result<()> {
    let relative = path
        .strip_prefix(root)
        .context("path is outside marketplace worktree")?;
    let relative = relative.to_str().context("tracked path is not UTF-8")?;
    let output = git_try(
        &["ls-tree", "-r", "--name-only", "HEAD", "--", relative],
        Some(root),
    )?;
    if !output.status.success()
        || String::from_utf8_lossy(&output.stdout)
            .lines()
            .all(|line| line != relative)
    {
        anyhow::bail!("required marketplace file is not tracked by HEAD");
    }
    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn git_origin_policy_accepts_public_https_ssh_and_scp() {
        for value in [
            "https://example.test/plugins.git",
            "ssh://git@example.test/plugins.git",
            "git@example.test:plugins.git",
        ] {
            assert!(validate_git_origin(value).is_ok(), "{value}");
        }
    }

    #[test]
    fn git_origin_policy_rejects_paths_http_and_credentials() {
        for value in [
            "/tmp/plugins.git",
            "file:///tmp/plugins.git",
            "http://example.test/plugins.git",
            "https://user:secret@example.test/plugins.git",
            "ssh://user:secret@example.test/plugins.git",
            "user:secret@example.test:plugins.git",
        ] {
            let error = validate_git_origin(value).unwrap_err().to_string();
            assert!(!error.contains("secret"), "{error}");
        }
    }

    #[test]
    fn mutation_wrappers_protect_openai_names() {
        let market = Marketplace {
            name: "openai-bundled".to_owned(),
            url: "https://example.test/openai.git".to_owned(),
            git_ref: "main".to_owned(),
            sparse: Vec::new(),
        };
        assert!(add_or_update_market(&market).is_err());
        assert!(remove_market("openai").is_err());
        assert!(remove_market("personal").is_err());
        assert!(install_plugin("browser@openai").is_err());
        assert!(remove_plugin("browser@openai-bundled").is_err());
        assert!(install_plugin("browser@personal").is_err());
    }
}
