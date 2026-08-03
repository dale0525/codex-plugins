use crate::artifact::Artifact;
use crate::model::{PlannedChange, PluginSpec, ProvisionReceipt};
use crate::reconcile::{marketplace_roots, plugin_marketplace};
use crate::transaction::OperationRecorder;
use anyhow::{Context, Result};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
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
    windows_shell: WindowsShell,
    #[serde(default)]
    arguments: Vec<String>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProvisionFreshness {
    Current,
    NeedsRun,
}
#[derive(Debug, Clone)]
pub struct RuntimeOperation {
    pub plugin_id: String,
    pub receipt: ProvisionReceipt,
    pub previous: Option<ProvisionReceipt>,
    pub uninstall: bool,
    #[allow(dead_code)]
    pub action_id: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum CompensationStatus {
    Intent,
    Running,
    Completed,
    ManualRequired,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CompensationStep {
    pub step_id: String,
    #[serde(default)]
    pub action_id: String,
    pub plugin_id: String,
    pub receipt: ProvisionReceipt,
    #[serde(default)]
    pub previous: Option<ProvisionReceipt>,
    pub uninstall: bool,
    pub status: CompensationStatus,
    #[serde(default)]
    pub message: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationLog {
    pub schema_version: u32,
    pub operation_id: String,
    pub kind: String,
    pub phase: String,
    pub actions: Vec<String>,
    #[serde(default)]
    pub action_records: Vec<OperationAction>,
    #[serde(default)]
    pub backup: Option<String>,
    pub recovery_required: bool,
    #[serde(default)]
    pub before_backup: Option<String>,
    #[serde(default)]
    pub target_state: Option<String>,
    #[serde(default)]
    pub before_state_digest: Option<String>,
    #[serde(default)]
    pub target_state_digest: Option<String>,
    #[serde(default)]
    pub supersedes: Option<String>,
    #[serde(default)]
    pub compensation_steps: Vec<CompensationStep>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ActionStatus {
    Intent,
    Running,
    Succeeded,
    Compensated,
    RecoveryRequired,
    Completed,
    Failed,
    ManualRequired,
    NotRun,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct OperationAction {
    #[serde(default)]
    pub action_id: String,
    pub plugin_id: String,
    pub receipt: ProvisionReceipt,
    #[serde(default)]
    pub previous: Option<ProvisionReceipt>,
    pub uninstall: bool,
    pub kind: String,
    pub status: ActionStatus,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub operation_kind: String,
    #[serde(default)]
    pub phase: String,
    #[serde(default)]
    pub before_receipt: Option<ProvisionReceipt>,
    #[serde(default)]
    pub after_receipt: Option<ProvisionReceipt>,
}
#[allow(dead_code)]
pub type Action = OperationAction;
include!("provision_wal.rs");
include!("provision_compensation.rs");
fn directory_sha256(root: &Path) -> Result<String> {
    fn visit(root: &Path, current: &Path, files: &mut Vec<(String, Vec<u8>)>) -> Result<()> {
        for entry in fs::read_dir(current)? {
            let entry = entry?;
            let path = entry.path();
            let kind = entry.file_type()?;
            if kind.is_dir() {
                visit(root, &path, files)?;
            } else if kind.is_file() {
                files.push((
                    path.strip_prefix(root)?
                        .to_string_lossy()
                        .replace('\\', "/"),
                    fs::read(&path)?,
                ));
            }
        }
        Ok(())
    }
    let mut files = Vec::new();
    visit(root, root, &mut files)?;
    files.sort_by(|left, right| left.0.cmp(&right.0));
    let mut digest = Sha256::new();
    for (path, bytes) in files {
        digest.update(path.as_bytes());
        digest.update([0]);
        digest.update(bytes);
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
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
fn load_provision(
    plugin: &PluginSpec,
) -> Result<(PathBuf, PathBuf, ProvisionSpec, String, String, String)> {
    let plugin_root = resolve_plugin_root(&plugin.id)?;
    let specification_path = safe_child(&plugin_root, ".codex-sync/provision.json")?;
    let specification_bytes = fs::read(&specification_path)
        .with_context(|| format!("read {}", specification_path.display()))?;
    let specification: ProvisionSpec = serde_json::from_slice(&specification_bytes)
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
    let spec_sha256 = hex::encode(Sha256::digest(&specification_bytes));
    let script_sha256 = hex::encode(Sha256::digest(
        &fs::read(&script).with_context(|| format!("read {}", script.display()))?,
    ));
    let dependencies_sha256 = directory_sha256(script.parent().unwrap_or(&plugin_root))?;
    Ok((
        plugin_root,
        script,
        specification,
        spec_sha256,
        script_sha256,
        dependencies_sha256,
    ))
}
pub fn provision_freshness(
    plugin: &PluginSpec,
    receipt: Option<&ProvisionReceipt>,
) -> Result<ProvisionFreshness> {
    let Some(receipt) = receipt else {
        return Ok(ProvisionFreshness::NeedsRun);
    };
    if receipt.schema_version != 2 {
        return Ok(ProvisionFreshness::NeedsRun);
    }
    if receipt.plugin_id != plugin.id {
        anyhow::bail!(
            "provision receipt drift detected for {}; receipt belongs to {}",
            plugin.id,
            receipt.plugin_id
        );
    }
    if verify_receipt(receipt).is_err() {
        return Ok(ProvisionFreshness::NeedsRun);
    }
    let (source_root, _script, _specification, spec_sha256, script_sha256, dependencies_sha256) =
        match load_provision(plugin) {
            Ok(source) => source,
            Err(_) => return Ok(ProvisionFreshness::NeedsRun),
        };
    let artifact_digest = match crate::artifact::digest(&source_root) {
        Ok(digest) => digest,
        Err(_) => return Ok(ProvisionFreshness::NeedsRun),
    };
    Ok(
        if receipt.artifact_digest == artifact_digest
            && receipt.spec_sha256 == spec_sha256
            && receipt.script_sha256 == script_sha256
            && receipt.dependencies_sha256 == dependencies_sha256
        {
            ProvisionFreshness::Current
        } else {
            ProvisionFreshness::NeedsRun
        },
    )
}
fn materialize_provision(
    plugin: &PluginSpec,
    data_home: &Path,
) -> Result<(Artifact, ProvisionSpec, PathBuf, String, String, String)> {
    let (source_root, source_script, _source_spec, _, _, _) = load_provision(plugin)?;
    let artifact = crate::artifact::materialize(&source_root, data_home)?;
    let relative_script = source_script.strip_prefix(&source_root)?;
    let script = artifact.root.join(relative_script);
    let spec_path = artifact.root.join(".codex-sync/provision.json");
    let spec_bytes = fs::read(&spec_path)?;
    let specification: ProvisionSpec = serde_json::from_slice(&spec_bytes)?;
    let spec_sha256 = hex::encode(Sha256::digest(&spec_bytes));
    let script_sha256 = hex::encode(Sha256::digest(&fs::read(&script)?));
    let dependencies_sha256 = directory_sha256(script.parent().unwrap_or(&artifact.root))?;
    Ok((
        artifact,
        specification,
        script,
        spec_sha256,
        script_sha256,
        dependencies_sha256,
    ))
}
pub fn materialize_new_provisioners(
    plugins: &[PluginSpec],
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
    data_home: &Path,
) -> Result<()> {
    plugins
        .iter()
        .filter(|p| p.enabled && p.auto_provision && !receipts.contains_key(&p.id))
        .try_for_each(|plugin| materialize_provision(plugin, data_home).map(|_| ()))?;
    Ok(())
}
#[derive(Debug)]
enum ScriptFailure {
    Spawn(anyhow::Error),
    Child(anyhow::Error),
}
fn preflight_script(
    plugin_root: &Path,
    script: &Path,
    specification: &ProvisionSpec,
    expected_script_sha256: &str,
    expected_dependencies_sha256: &str,
) -> Result<(String, Vec<String>)> {
    let actual_script_sha256 = hex::encode(Sha256::digest(&fs::read(script)?));
    anyhow::ensure!(
        actual_script_sha256 == expected_script_sha256,
        "provision script changed before execution: {}",
        script.display()
    );
    let actual_dependencies_sha256 = directory_sha256(script.parent().unwrap_or(plugin_root))?;
    anyhow::ensure!(
        actual_dependencies_sha256 == expected_dependencies_sha256,
        "provision dependencies changed before execution: {}",
        script.display()
    );
    launcher_for(cfg!(windows), specification.windows_shell)
}
fn run_script_with_launcher(
    plugin_id: &str,
    plugin_root: &Path,
    script: &Path,
    launcher: &str,
    launcher_arguments: &[String],
    arguments: &[String],
) -> std::result::Result<String, ScriptFailure> {
    let mut command = Command::new(launcher);
    command
        .args(launcher_arguments)
        .arg(script)
        .args(arguments)
        .current_dir(plugin_root)
        .env("PLUGIN_ROOT", plugin_root)
        .env_remove("CODEX_SYNC_GITHUB_TOKEN")
        .env_remove("GITHUB_TOKEN")
        .env_remove("GH_TOKEN");
    let child = command.spawn().map_err(|error| {
        ScriptFailure::Spawn(anyhow::anyhow!("run provisioner for {plugin_id}: {error}"))
    })?;
    let output = child.wait_with_output().map_err(|error| {
        ScriptFailure::Child(anyhow::anyhow!("wait for provisioner {plugin_id}: {error}"))
    })?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(ScriptFailure::Child(anyhow::anyhow!(
            "provisioner for {plugin_id} failed: {}",
            detail.trim()
        )));
    }
    let detail = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    let operation = match arguments.first().map(String::as_str) {
        Some("uninstall") => "uninstalled",
        _ => "provisioned",
    };
    Ok(if detail.is_empty() {
        format!("{operation} {plugin_id}")
    } else {
        format!("{operation} {plugin_id}: {detail}")
    })
}
fn run_script(
    plugin_id: &str,
    plugin_root: &Path,
    script: &Path,
    specification: &ProvisionSpec,
    arguments: &[String],
    expected_script_sha256: &str,
    expected_dependencies_sha256: &str,
) -> Result<String> {
    let (launcher, launcher_arguments) = preflight_script(
        plugin_root,
        script,
        specification,
        expected_script_sha256,
        expected_dependencies_sha256,
    )?;
    run_script_with_launcher(
        plugin_id,
        plugin_root,
        script,
        &launcher,
        &launcher_arguments,
        arguments,
    )
    .map_err(|failure| match failure {
        ScriptFailure::Spawn(error) | ScriptFailure::Child(error) => error,
    })
}
fn return_action_to_intent(
    recorder: &mut OperationRecorder,
    action_id: &str,
    message: String,
) -> Result<()> {
    let action = recorder
        .log
        .action_records
        .iter_mut()
        .find(|action| action.action_id == action_id)
        .context("operation action ID not found")?;
    action.status = ActionStatus::Intent;
    action.phase = "intent".to_owned();
    action.message = Some(message);
    recorder.persist()
}
pub fn run_auto_provisioners_recorded(
    plugins: &[PluginSpec],
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
    planned_targets: &BTreeSet<String>,
    operations: &mut Vec<RuntimeOperation>,
    data_home: &Path,
    mut recorder: Option<&mut OperationRecorder>,
) -> Result<(
    Vec<String>,
    std::collections::BTreeMap<String, ProvisionReceipt>,
)> {
    let mut next = receipts.clone();
    let mut messages = Vec::new();
    for plugin in plugins.iter().filter(|plugin| {
        planned_targets.contains(&plugin.id) && plugin.enabled && plugin.auto_provision
    }) {
        let previous = receipts.get(&plugin.id);
        if provision_freshness(plugin, previous)? == ProvisionFreshness::Current {
            continue;
        }
        let (artifact, specification, script, spec_sha256, script_sha256, dependencies_sha256) =
            materialize_provision(plugin, data_home)?;
        let plugin_root = artifact.root.clone();
        let (launcher, launcher_arguments) = preflight_script(
            &plugin_root,
            &script,
            &specification,
            &script_sha256,
            &dependencies_sha256,
        )?;
        let next_receipt = ProvisionReceipt {
            schema_version: 2,
            plugin_id: plugin.id.clone(),
            artifact_digest: artifact.digest.clone(),
            artifact_root: artifact.root.to_string_lossy().into_owned(),
            spec_sha256: spec_sha256.clone(),
            script_sha256: script_sha256.clone(),
            dependencies_sha256: dependencies_sha256.clone(),
            setup_args: specification.arguments.clone(),
            uninstall_args: vec!["uninstall".to_owned()],
            windows_shell: format!("{:?}", specification.windows_shell),
            plugin_root: plugin_root.to_string_lossy().into_owned(),
            script: script.to_string_lossy().into_owned(),
            provisioned_at: Utc::now().to_rfc3339(),
        };
        operations.push(RuntimeOperation {
            plugin_id: plugin.id.clone(),
            receipt: next_receipt.clone(),
            previous: previous.cloned(),
            uninstall: false,
            action_id: None,
        });
        let action_id = if let Some(log) = recorder.as_deref_mut() {
            Some(log.intent(operations.last().expect("operation just pushed"))?)
        } else {
            None
        };
        if let (Some(operation), Some(id)) = (operations.last_mut(), action_id.as_ref()) {
            operation.action_id = Some(id.clone());
        }
        if let Some(log) = recorder.as_deref_mut() {
            log.running(action_id.as_deref().expect("recorded action"))?;
        }
        let message = match run_script_with_launcher(
            &plugin.id,
            &plugin_root,
            &script,
            &launcher,
            &launcher_arguments,
            &specification.arguments,
        ) {
            Ok(message) => message,
            Err(ScriptFailure::Spawn(error)) => {
                if let Some(log) = recorder.as_deref_mut() {
                    return_action_to_intent(
                        log,
                        action_id.as_deref().expect("recorded action"),
                        format!("{error:#}"),
                    )?;
                }
                return Err(error);
            }
            Err(ScriptFailure::Child(error)) => {
                if let Some(log) = recorder.as_deref_mut() {
                    log.recovery_required(
                        action_id.as_deref().expect("recorded action"),
                        format!("{error:#}"),
                    )
                    .with_context(|| {
                        format!("provisioner for {} failed and manual-required checkpoint could not be persisted", plugin.id)
                    })?;
                }
                return Err(error);
            }
        };
        if let Some(log) = recorder.as_deref_mut() {
            log.succeeded(
                action_id.as_deref().expect("recorded action"),
                message.clone(),
            )?;
        }
        messages.push(message);
        next.insert(plugin.id.clone(), next_receipt);
    }
    Ok((messages, next))
}
pub fn validate_auto_provisioners(
    plugins: &[PluginSpec],
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
) -> Result<()> {
    for plugin in plugins.iter().filter(|p| p.enabled && p.auto_provision) {
        if let Some(receipt) = receipts.get(&plugin.id) {
            anyhow::ensure!(
                receipt.plugin_id == plugin.id,
                "provision receipt drift detected for {}; receipt belongs to {}",
                plugin.id,
                receipt.plugin_id
            );
        }
    }
    Ok(())
}
pub fn migrate_receipts(
    receipts: &mut std::collections::BTreeMap<String, ProvisionReceipt>,
    data_home: &Path,
) -> Result<bool> {
    let mut validated = Vec::new();
    for (key, receipt) in receipts.iter() {
        if receipt.schema_version >= 2 {
            continue;
        }
        if receipt.plugin_id.is_empty() {
            anyhow::bail!("legacy provision receipt has empty plugin ID; refusing migration");
        }
        if key != &receipt.plugin_id {
            anyhow::bail!(
                "legacy provision receipt key mismatch for {}; refusing migration",
                receipt.plugin_id
            );
        }
        for (label, value) in [
            ("spec_sha256", &receipt.spec_sha256),
            ("script_sha256", &receipt.script_sha256),
            ("dependencies_sha256", &receipt.dependencies_sha256),
        ] {
            if !is_sha256(value) {
                anyhow::bail!(
                    "legacy provision receipt for {} has missing or invalid {label}; refusing migration",
                    receipt.plugin_id
                );
            }
        }
        let source_root = PathBuf::from(&receipt.plugin_root);
        let (
            source_root,
            source_script,
            specification,
            spec_sha256,
            script_sha256,
            dependencies_sha256,
        ) = load_provision_from_root(&receipt.plugin_id, &source_root)?;
        if receipt.script != source_script.to_string_lossy()
            || receipt.plugin_root != source_root.to_string_lossy()
        {
            anyhow::bail!(
                "legacy provision receipt path drift detected for {}; refusing migration",
                receipt.plugin_id
            );
        }
        for (label, recorded, current) in [
            ("spec_sha256", &receipt.spec_sha256, &spec_sha256),
            ("script_sha256", &receipt.script_sha256, &script_sha256),
            (
                "dependencies_sha256",
                &receipt.dependencies_sha256,
                &dependencies_sha256,
            ),
        ] {
            if recorded != current {
                anyhow::bail!(
                    "legacy provision receipt drift detected for {} ({label})",
                    receipt.plugin_id
                );
            }
        }
        validated.push((
            receipt.clone(),
            source_root,
            source_script,
            specification,
            spec_sha256,
            script_sha256,
            dependencies_sha256,
        ));
    }
    if validated.is_empty() {
        return Ok(false);
    }
    let mut migrated = receipts.clone();
    for (
        mut receipt,
        source_root,
        source_script,
        specification,
        spec_sha256,
        script_sha256,
        dependencies_sha256,
    ) in validated
    {
        let artifact = crate::artifact::materialize(&source_root, data_home)?;
        let relative = source_script.strip_prefix(&source_root)?;
        let artifact_script = artifact.root.join(relative);
        receipt.schema_version = 2;
        receipt.artifact_digest = artifact.digest;
        receipt.artifact_root = artifact.root.to_string_lossy().into_owned();
        receipt.plugin_root = receipt.artifact_root.clone();
        receipt.script = artifact_script.to_string_lossy().into_owned();
        receipt.spec_sha256 = spec_sha256;
        receipt.script_sha256 = script_sha256;
        receipt.dependencies_sha256 = dependencies_sha256;
        receipt.setup_args = specification.arguments;
        receipt.uninstall_args = vec!["uninstall".to_owned()];
        receipt.windows_shell = format!("{:?}", specification.windows_shell);
        migrated.insert(receipt.plugin_id.clone(), receipt);
    }
    *receipts = migrated;
    Ok(true)
}
fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.as_bytes().iter().all(u8::is_ascii_hexdigit)
}
fn load_provision_from_root(
    plugin_id: &str,
    plugin_root: &Path,
) -> Result<(PathBuf, PathBuf, ProvisionSpec, String, String, String)> {
    let plugin_root = fs::canonicalize(plugin_root)
        .with_context(|| format!("resolve legacy provision root {}", plugin_root.display()))?;
    let spec_path = safe_child(&plugin_root, ".codex-sync/provision.json")?;
    let spec_bytes = fs::read(&spec_path)?;
    let specification: ProvisionSpec = serde_json::from_slice(&spec_bytes)?;
    if specification.schema_version != 1 || specification.risk != "high" {
        anyhow::bail!("legacy provision specification must declare schema 1 and high risk");
    }
    let selected = safe_child(
        &plugin_root,
        if cfg!(windows) {
            specification.windows_script.trim_start_matches("./")
        } else {
            specification.posix_script.trim_start_matches("./")
        },
    )?;
    if !selected.is_file() {
        anyhow::bail!("legacy provision script missing for {plugin_id}");
    }
    let spec_sha256 = hex::encode(Sha256::digest(spec_bytes));
    let script_sha256 = hex::encode(Sha256::digest(fs::read(&selected)?));
    let dependencies_sha256 = directory_sha256(selected.parent().unwrap_or(&plugin_root))?;
    Ok((
        plugin_root,
        selected,
        specification,
        spec_sha256,
        script_sha256,
        dependencies_sha256,
    ))
}
pub fn removal_ids(
    changes: &[PlannedChange],
    desired: &[PluginSpec],
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
) -> Vec<String> {
    changes
        .iter()
        .filter(|change| change.kind == "plugin" && change.summary.contains("remove plugin"))
        .map(|change| change.target.clone())
        .chain(
            receipts
                .keys()
                .filter(|id| {
                    !desired.iter().any(|plugin| {
                        plugin.id.as_str() == id.as_str() && plugin.enabled && plugin.auto_provision
                    })
                })
                .cloned(),
        )
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}
pub fn run_removal_provisioners_recorded(
    changes: &[PlannedChange],
    desired: &[PluginSpec],
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
    operations: &mut Vec<RuntimeOperation>,
    recorder: &mut OperationRecorder,
) -> Result<Vec<String>> {
    run_uninstallers_recorded(
        &removal_ids(changes, desired, receipts),
        receipts,
        operations,
        Some(recorder),
    )
}
pub fn retain_receipts(
    receipts: std::collections::BTreeMap<String, ProvisionReceipt>,
    desired: &[PluginSpec],
) -> std::collections::BTreeMap<String, ProvisionReceipt> {
    let desired_ids: BTreeSet<&str> = desired
        .iter()
        .filter(|plugin| plugin.enabled && plugin.auto_provision)
        .map(|plugin| plugin.id.as_str())
        .collect();
    receipts
        .into_iter()
        .filter(|(id, _)| desired_ids.contains(id.as_str()))
        .collect()
}
#[allow(dead_code)]
pub fn run_uninstallers(
    plugin_ids: &[String],
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
    operations: &mut Vec<RuntimeOperation>,
) -> Result<Vec<String>> {
    run_uninstallers_recorded(plugin_ids, receipts, operations, None)
}
pub fn run_uninstallers_recorded(
    plugin_ids: &[String],
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
    operations: &mut Vec<RuntimeOperation>,
    mut recorder: Option<&mut OperationRecorder>,
) -> Result<Vec<String>> {
    let mut messages = Vec::new();
    for plugin_id in plugin_ids {
        let Some(receipt) = receipts.get(plugin_id) else {
            continue;
        };
        if receipt.plugin_id != *plugin_id {
            anyhow::bail!("provision receipt key mismatch for {plugin_id}");
        }
        if receipt.schema_version >= 2 {
            verify_receipt(receipt)?;
            operations.push(RuntimeOperation {
                plugin_id: plugin_id.clone(),
                receipt: receipt.clone(),
                previous: Some(receipt.clone()),
                uninstall: true,
                action_id: None,
            });
            let action_id = if let Some(log) = recorder.as_deref_mut() {
                Some(log.intent(operations.last().expect("operation just pushed"))?)
            } else {
                None
            };
            if let (Some(operation), Some(id)) = (operations.last_mut(), action_id.as_ref()) {
                operation.action_id = Some(id.clone());
            }
            if let Some(log) = recorder.as_deref_mut() {
                log.running(action_id.as_deref().expect("recorded action"))?;
            }
            let message = match execute_receipt(receipt, true) {
                Ok(message) => message,
                Err(error) => {
                    if let Some(log) = recorder.as_deref_mut() {
                        log.recovery_required(
                            action_id.as_deref().expect("recorded action"),
                            format!("{error:#}"),
                        )
                        .with_context(|| {
                            format!(
                                "uninstaller for {plugin_id} failed and manual-required checkpoint could not be persisted"
                            )
                        })?;
                    }
                    return Err(error);
                }
            };
            if let Some(log) = recorder.as_deref_mut() {
                log.succeeded(
                    action_id.as_deref().expect("recorded action"),
                    message.clone(),
                )?;
            }
            messages.push(message);
            continue;
        }
        let plugin_root = PathBuf::from(&receipt.plugin_root);
        let script = PathBuf::from(&receipt.script);
        if !plugin_root.is_dir() || !script.is_file() {
            anyhow::bail!(
                "provision receipt for {plugin_id} points to missing runtime; refusing to remove plugin"
            );
        }
        let specification_path = plugin_root.join(".codex-sync/provision.json");
        let specification_bytes = fs::read(&specification_path)
            .with_context(|| format!("read {}", specification_path.display()))?;
        let specification: ProvisionSpec = serde_json::from_slice(&specification_bytes)
            .with_context(|| format!("parse {}", specification_path.display()))?;
        let script_sha256 = hex::encode(Sha256::digest(&fs::read(&script)?));
        let dependencies_sha256 = directory_sha256(script.parent().unwrap_or(&plugin_root))?;
        if hex::encode(Sha256::digest(&specification_bytes)) != receipt.spec_sha256
            || script_sha256 != receipt.script_sha256
            || dependencies_sha256 != receipt.dependencies_sha256
        {
            anyhow::bail!(
                "provision receipt drift detected for {plugin_id}; refusing to uninstall"
            );
        }
        operations.push(RuntimeOperation {
            plugin_id: plugin_id.clone(),
            receipt: receipt.clone(),
            previous: Some(receipt.clone()),
            uninstall: true,
            action_id: None,
        });
        let action_id = if let Some(log) = recorder.as_deref_mut() {
            Some(log.intent(operations.last().expect("operation just pushed"))?)
        } else {
            None
        };
        if let (Some(operation), Some(id)) = (operations.last_mut(), action_id.as_ref()) {
            operation.action_id = Some(id.clone());
        }
        if let Some(log) = recorder.as_deref_mut() {
            log.running(action_id.as_deref().expect("recorded action"))?;
        }
        let message = match run_script(
            plugin_id,
            &plugin_root,
            &script,
            &specification,
            &["uninstall".to_owned()],
            &script_sha256,
            &dependencies_sha256,
        ) {
            Ok(message) => message,
            Err(error) => {
                if let Some(log) = recorder.as_deref_mut() {
                    log.recovery_required(
                        action_id.as_deref().expect("recorded action"),
                        format!("{error:#}"),
                    )
                    .with_context(|| {
                        format!("uninstaller for {plugin_id} failed and manual-required checkpoint could not be persisted")
                    })?;
                }
                return Err(error);
            }
        };
        if let Some(log) = recorder.as_deref_mut() {
            log.succeeded(
                action_id.as_deref().expect("recorded action"),
                message.clone(),
            )?;
        }
        messages.push(message);
    }
    Ok(messages)
}
#[allow(dead_code)]
pub fn restore_provisioners(
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
) -> Result<()> {
    restore_provisioners_recorded(receipts, &mut Vec::new(), None)
}
pub fn restore_provisioners_recorded(
    receipts: &std::collections::BTreeMap<String, ProvisionReceipt>,
    operations: &mut Vec<RuntimeOperation>,
    mut recorder: Option<&mut OperationRecorder>,
) -> Result<()> {
    let mut failures = Vec::new();
    for receipt in receipts.values() {
        let operation = RuntimeOperation {
            plugin_id: receipt.plugin_id.clone(),
            receipt: receipt.clone(),
            previous: None,
            uninstall: false,
            action_id: None,
        };
        operations.push(operation);
        let action_id = if let Some(log) = recorder.as_deref_mut() {
            Some(log.intent(operations.last().expect("operation just pushed"))?)
        } else {
            None
        };
        if let (Some(operation), Some(id)) = (operations.last_mut(), action_id.as_ref()) {
            operation.action_id = Some(id.clone());
        }
        if let Some(log) = recorder.as_deref_mut() {
            log.running(action_id.as_deref().expect("recorded action"))?;
        }
        match execute_receipt(receipt, false) {
            Ok(message) => {
                if let Some(log) = recorder.as_deref_mut() {
                    log.succeeded(action_id.as_deref().expect("recorded action"), message)?;
                }
            }
            Err(error) => {
                if let (Some(log), Some(id)) = (recorder.as_deref_mut(), action_id.as_deref()) {
                    log.recovery_required(id, format!("{error:#}"))?;
                }
                failures.push(format!("{}: {error:#}", receipt.plugin_id));
            }
        }
    }
    if !failures.is_empty() {
        anyhow::bail!("runtime restoration failed: {}", failures.join("; "));
    }
    Ok(())
}
fn execute_receipt(receipt: &ProvisionReceipt, uninstall: bool) -> Result<String> {
    let (root, script, spec, script_sha256, dependencies_sha256) = verify_receipt(receipt)?;
    let args = if uninstall {
        if receipt.uninstall_args.is_empty() {
            vec!["uninstall".to_owned()]
        } else {
            receipt.uninstall_args.clone()
        }
    } else if receipt.setup_args.is_empty() {
        spec.arguments.clone()
    } else {
        receipt.setup_args.clone()
    };
    run_script(
        &receipt.plugin_id,
        &root,
        &script,
        &spec,
        &args,
        &script_sha256,
        &dependencies_sha256,
    )
}
fn verify_receipt(
    receipt: &ProvisionReceipt,
) -> Result<(PathBuf, PathBuf, ProvisionSpec, String, String)> {
    let root = PathBuf::from(&receipt.plugin_root);
    let script = PathBuf::from(&receipt.script);
    if !root.is_dir() || !script.is_file() {
        anyhow::bail!(
            "provision receipt for {} points to missing runtime",
            receipt.plugin_id
        );
    }
    let spec_path = root.join(".codex-sync/provision.json");
    let spec_bytes = fs::read(&spec_path)?;
    let spec: ProvisionSpec = serde_json::from_slice(&spec_bytes)?;
    let selected = root.join(if cfg!(windows) {
        spec.windows_script.trim_start_matches("./")
    } else {
        spec.posix_script.trim_start_matches("./")
    });
    if fs::canonicalize(&selected)? != fs::canonicalize(&script)? {
        anyhow::bail!("provision receipt script does not match selected script");
    }
    let spec_sha256 = hex::encode(Sha256::digest(&spec_bytes));
    let script_sha256 = hex::encode(Sha256::digest(&fs::read(&script)?));
    let dependencies_sha256 = directory_sha256(script.parent().unwrap_or(&root))?;
    if spec_sha256 != receipt.spec_sha256
        || script_sha256 != receipt.script_sha256
        || dependencies_sha256 != receipt.dependencies_sha256
    {
        anyhow::bail!("provision receipt drift detected for {}", receipt.plugin_id);
    }
    if receipt.schema_version >= 2 && receipt.artifact_root != root.to_string_lossy() {
        anyhow::bail!(
            "provision receipt artifact root mismatch for {}",
            receipt.plugin_id
        );
    }
    if receipt.schema_version >= 2 && receipt.artifact_digest != crate::artifact::digest(&root)? {
        anyhow::bail!(
            "provision artifact digest mismatch for {}",
            receipt.plugin_id
        );
    }
    Ok((root, script, spec, script_sha256, dependencies_sha256))
}
#[cfg(test)]
#[path = "provision_tests.rs"]
mod tests;
