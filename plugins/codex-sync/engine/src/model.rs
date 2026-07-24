use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

pub const SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RepositoryRef {
    pub owner: String,
    pub name: String,
    #[serde(default = "default_git_ref")]
    pub git_ref: String,
}

fn default_git_ref() -> String {
    "main".to_owned()
}

impl RepositoryRef {
    pub fn parse(value: &str, git_ref: String) -> anyhow::Result<Self> {
        let (owner, name) = value
            .split_once('/')
            .ok_or_else(|| anyhow::anyhow!("repository must use owner/name syntax"))?;
        if owner.is_empty()
            || name.is_empty()
            || owner.contains(['/', '\\'])
            || name.contains(['/', '\\'])
        {
            anyhow::bail!("repository must use owner/name syntax");
        }
        Ok(Self {
            owner: owner.to_owned(),
            name: name.trim_end_matches(".git").to_owned(),
            git_ref,
        })
    }

    pub fn slug(&self) -> String {
        format!("{}/{}", self.owner, self.name)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalState {
    pub schema_version: u32,
    pub repository: RepositoryRef,
    pub device_id: String,
    #[serde(default)]
    pub github_client_id: Option<String>,
    #[serde(default)]
    pub last_fetched_commit: Option<String>,
    #[serde(default)]
    pub fetched_repository_sha256: Option<String>,
    #[serde(default)]
    pub last_applied_commit: Option<String>,
    #[serde(default)]
    pub managed_paths: Vec<Vec<String>>,
    #[serde(default)]
    pub latest_backup: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RepositoryManifest {
    pub schema_version: u32,
    #[serde(default = "default_agents_path")]
    pub agents: String,
    #[serde(default = "default_common_config_path")]
    pub common_config: String,
    #[serde(default = "default_devices_path")]
    pub devices: String,
    #[serde(default = "default_marketplaces_path")]
    pub marketplaces: String,
    #[serde(default = "default_plugins_path")]
    pub plugins: String,
    #[serde(default = "default_providers_path")]
    pub providers: String,
}

fn default_agents_path() -> String {
    "AGENTS.md".to_owned()
}

fn default_common_config_path() -> String {
    "config/common.toml".to_owned()
}

fn default_devices_path() -> String {
    "devices".to_owned()
}

fn default_marketplaces_path() -> String {
    "marketplaces.toml".to_owned()
}

fn default_plugins_path() -> String {
    "plugins.toml".to_owned()
}

fn default_providers_path() -> String {
    "providers.toml".to_owned()
}

impl RepositoryManifest {
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.schema_version != SCHEMA_VERSION {
            anyhow::bail!(
                "unsupported repository schema version {}; expected {}",
                self.schema_version,
                SCHEMA_VERSION
            );
        }
        for value in [
            &self.agents,
            &self.common_config,
            &self.devices,
            &self.marketplaces,
            &self.plugins,
            &self.providers,
        ] {
            validate_relative_path(value)?;
        }
        Ok(())
    }
}

fn validate_relative_path(value: &str) -> anyhow::Result<()> {
    let path = std::path::Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        anyhow::bail!("repository manifest path must stay inside the repository: {value}");
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct MarketplaceFile {
    #[serde(default)]
    pub marketplaces: Vec<MarketplaceSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "source", rename_all = "kebab-case")]
pub enum MarketplaceSpec {
    Git {
        name: String,
        url: String,
        #[serde(default = "default_git_ref")]
        git_ref: String,
        #[serde(default)]
        sparse: Vec<String>,
    },
    GithubSnapshot {
        name: String,
        repository: String,
        #[serde(default = "default_git_ref")]
        git_ref: String,
    },
}

impl MarketplaceSpec {
    pub fn name(&self) -> &str {
        match self {
            Self::Git { name, .. } | Self::GithubSnapshot { name, .. } => name,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PluginFile {
    #[serde(default)]
    pub plugins: Vec<PluginSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginSpec {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ProviderFile {
    #[serde(default)]
    pub providers: BTreeMap<String, toml::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingPlan {
    pub id: String,
    pub generated_at: String,
    pub commit: String,
    pub device_id: String,
    pub base_config_sha256: String,
    pub base_agents_sha256: String,
    pub repository_sha256: String,
    pub high_risk: bool,
    pub changes: Vec<PlannedChange>,
    pub managed_paths: Vec<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlannedChange {
    pub risk: Risk,
    pub kind: String,
    pub target: String,
    pub summary: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum Risk {
    Low,
    High,
}

#[derive(Debug)]
pub struct Paths {
    pub data_home: PathBuf,
    pub state_file: PathBuf,
    pub lock_file: PathBuf,
    pub repository_dir: PathBuf,
    pub marketplaces_dir: PathBuf,
    pub backups_dir: PathBuf,
    pub pending_plan: PathBuf,
    pub codex_home: PathBuf,
}
