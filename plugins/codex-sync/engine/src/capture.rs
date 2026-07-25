use std::collections::BTreeSet;
use std::fs;

use anyhow::{Context, Result};

use crate::app::{load_repository_manifest, validate_desired_state, validate_state};
use crate::config::{
    capture_current_providers, capture_existing_managed_values, load_managed_values,
    managed_value_paths, read_current_config,
};
use crate::model::{MarketplaceFile, MarketplaceSpec, PluginFile, PluginSpec};
use crate::profiles::{current_profile_bytes, load_agent_profiles};
use crate::reconcile::{installed_plugins, is_openai_managed_marketplace, plugin_marketplace};
use crate::storage::{
    acquire_lock, atomic_write, copy_tree, load_state, read_optional_toml, replace_tree_atomically,
    resolve_paths, tree_sha256,
};

#[derive(Debug, Default)]
struct PluginCapture {
    captured: Vec<String>,
    excluded_openai: Vec<String>,
    skipped_nonportable: Vec<String>,
    added_marketplaces: Vec<String>,
}

pub fn capture() -> Result<()> {
    let paths = resolve_paths()?;
    let _lock = acquire_lock(&paths)?;
    let state = load_state(&paths)?;
    validate_state(&state)?;
    state
        .last_fetched_commit
        .as_ref()
        .context("nothing has been fetched; run sync first")?;
    let expected_digest = state
        .fetched_repository_sha256
        .as_deref()
        .context("repository cache has no fetched digest; run sync first")?;
    if !paths.repository_dir.is_dir() {
        anyhow::bail!("repository cache is missing; run sync first");
    }
    let before_digest = tree_sha256(&paths.repository_dir)?;
    if before_digest != expected_digest {
        anyhow::bail!(
            "local repository cache has unpublished edits; review and publish them or synchronize before capturing current device state"
        );
    }

    let temporary =
        tempfile::tempdir_in(&paths.data_home).context("create capture transaction directory")?;
    let staged = temporary.path().join("repository");
    copy_tree(&paths.repository_dir, &staged)?;
    let manifest = load_repository_manifest(&staged)?;
    let current_config = read_current_config(&paths.codex_home)?;

    let device_file = staged
        .join(&manifest.devices)
        .join(format!("{}.toml", state.device_id));
    let device_paths = managed_value_paths(&device_file)?;
    capture_existing_managed_values(
        &current_config,
        &staged.join(&manifest.common_config),
        &device_paths,
    )?;
    capture_existing_managed_values(&current_config, &device_file, &BTreeSet::new())?;
    capture_current_providers(&current_config, &staged.join(&manifest.providers))?;

    let current_agents =
        fs::read(paths.codex_home.join("AGENTS.md")).context("read current global AGENTS.md")?;
    atomic_write(&staged.join(&manifest.agents), &current_agents)?;

    let repository_profiles = load_agent_profiles(&staged, &manifest.agent_profiles)?;
    for name in repository_profiles.keys() {
        let target = staged
            .join(&manifest.agent_profiles)
            .join(format!("{name}.toml"));
        match current_profile_bytes(&paths.codex_home.join("agents"), name)? {
            Some(bytes) => atomic_write(&target, &bytes)?,
            None => fs::remove_file(&target)
                .with_context(|| format!("remove missing captured profile {}.toml", name))?,
        }
    }

    let plugin_capture = capture_plugins(&staged, &manifest, &current_config)?;

    load_managed_values(&staged, &manifest, &state.device_id)?;
    load_agent_profiles(&staged, &manifest.agent_profiles)?;
    let marketplaces: MarketplaceFile = read_optional_toml(&staged.join(&manifest.marketplaces))?;
    let plugins: PluginFile = read_optional_toml(&staged.join(&manifest.plugins))?;
    validate_desired_state(&marketplaces, &plugins)?;

    let after_digest = tree_sha256(&staged)?;
    if after_digest == before_digest {
        println!("Current device state already matches the repository cache");
        print_plugin_capture(&plugin_capture);
        return Ok(());
    }
    replace_tree_atomically(&staged, &paths.repository_dir)?;
    match fs::remove_file(&paths.pending_plan) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error).context("invalidate pending synchronization plan"),
    }
    println!("Captured current device state into the local repository cache");
    println!("- updated managed config values, providers, AGENTS.md, and managed profiles");
    print_plugin_capture(&plugin_capture);
    println!("Review the repository diff, run `codex-sync doctor`, then publish after approval");
    Ok(())
}

fn capture_plugins(
    repository: &std::path::Path,
    manifest: &crate::model::RepositoryManifest,
    current_config: &str,
) -> Result<PluginCapture> {
    let mut report = PluginCapture::default();
    let mut marketplaces: MarketplaceFile =
        read_optional_toml(&repository.join(&manifest.marketplaces))?;
    marketplaces
        .marketplaces
        .retain(|spec| !is_openai_managed_marketplace(spec.name()));
    let mut marketplace_names: BTreeSet<String> = marketplaces
        .marketplaces
        .iter()
        .map(|spec| spec.name().to_owned())
        .collect();
    let current_config = current_config
        .parse::<toml::Value>()
        .context("parse current Codex config.toml")?;
    let mut desired = BTreeSet::new();

    for plugin in installed_plugins()?
        .into_iter()
        .filter(|plugin| plugin.installed && plugin.enabled)
    {
        let marketplace = plugin_marketplace(&plugin.plugin_id)?;
        if is_openai_managed_marketplace(marketplace) {
            report.excluded_openai.push(plugin.plugin_id);
            continue;
        }
        if !marketplace_names.contains(marketplace) {
            let Some(spec) = git_marketplace_from_config(&current_config, marketplace) else {
                report.skipped_nonportable.push(plugin.plugin_id);
                continue;
            };
            marketplaces.marketplaces.push(spec);
            marketplace_names.insert(marketplace.to_owned());
            report.added_marketplaces.push(marketplace.to_owned());
        }
        desired.insert(plugin.plugin_id.clone());
        report.captured.push(plugin.plugin_id);
    }

    marketplaces
        .marketplaces
        .sort_by(|left, right| left.name().cmp(right.name()));
    let plugins = PluginFile {
        plugins: desired
            .into_iter()
            .map(|id| PluginSpec { id, enabled: true })
            .collect(),
    };
    atomic_write(
        &repository.join(&manifest.marketplaces),
        toml::to_string_pretty(&marketplaces)?.as_bytes(),
    )?;
    atomic_write(
        &repository.join(&manifest.plugins),
        toml::to_string_pretty(&plugins)?.as_bytes(),
    )?;
    Ok(report)
}

fn git_marketplace_from_config(current: &toml::Value, name: &str) -> Option<MarketplaceSpec> {
    let table = current
        .as_table()?
        .get("marketplaces")?
        .as_table()?
        .get(name)?
        .as_table()?;
    if table.get("source_type")?.as_str()? != "git" {
        return None;
    }
    let url = table.get("source")?.as_str()?.to_owned();
    let git_ref = table
        .get("ref")
        .and_then(toml::Value::as_str)
        .unwrap_or("main")
        .to_owned();
    Some(MarketplaceSpec::Git {
        name: name.to_owned(),
        url,
        git_ref,
        sparse: Vec::new(),
    })
}

fn print_plugin_capture(report: &PluginCapture) {
    println!(
        "- captured {} installed non-OpenAI plugin(s)",
        report.captured.len()
    );
    println!(
        "- excluded {} OpenAI-managed plugin(s)",
        report.excluded_openai.len()
    );
    if !report.added_marketplaces.is_empty() {
        println!(
            "- added portable Git marketplace declaration(s): {}",
            report.added_marketplaces.join(", ")
        );
    }
    for plugin in &report.skipped_nonportable {
        println!(
            "warning: skipped {plugin}; its marketplace is not declared and has no portable Git source"
        );
    }
}
