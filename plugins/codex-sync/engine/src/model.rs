use std::path::PathBuf;

use anyhow::Result;
use serde::{Deserialize, Serialize};

pub const STATE_SCHEMA_VERSION: u32 = 3;
#[allow(dead_code)]
pub const REPOSITORY_SCHEMA_VERSION: u32 = 3;

/// The local state intentionally contains only binding and convergence data.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LocalState {
    #[serde(default = "state_schema_version")]
    pub schema_version: u32,
    pub repository: String,
    pub branch: String,
    pub device: String,
    #[serde(default)]
    pub last_applied_commit: Option<String>,
    #[serde(default)]
    pub managed_paths: Vec<Vec<String>>,
    #[serde(default)]
    pub managed_profiles: Vec<String>,
    #[serde(default)]
    pub migration_cleanup_pending: bool,
    #[serde(default)]
    pub migration_pushed_commit: Option<String>,
    #[serde(default = "default_true")]
    pub converged: bool,
}

fn state_schema_version() -> u32 {
    STATE_SCHEMA_VERSION
}

fn default_true() -> bool {
    true
}

/// A compatibility reader for the v0.4 state file.  It is deliberately not
/// persisted: setup normalizes it into [`LocalState`].
#[allow(dead_code)]
#[derive(Debug, Deserialize)]
pub struct LegacyState {
    #[serde(default)]
    pub schema_version: u32,
    #[serde(default)]
    pub repository: Option<LegacyRepository>,
    #[serde(default)]
    pub device_id: Option<String>,
    #[serde(default)]
    pub device: Option<String>,
    #[serde(default)]
    pub branch: Option<String>,
    #[serde(default)]
    pub managed_paths: Vec<Vec<String>>,
    #[serde(default, alias = "managed_agent_profiles")]
    pub managed_profiles: Vec<String>,
    #[serde(default)]
    pub last_applied_commit: Option<String>,
    #[serde(default)]
    pub migration_cleanup_pending: bool,
}

#[derive(Debug, Deserialize)]
pub struct LegacyRepository {
    #[serde(default)]
    pub owner: String,
    #[serde(default)]
    pub name: String,
    #[serde(default, alias = "git_ref")]
    pub branch: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
}

impl LocalState {
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != STATE_SCHEMA_VERSION {
            anyhow::bail!(
                "unsupported local state schema {}; expected {}",
                self.schema_version,
                STATE_SCHEMA_VERSION
            );
        }
        if self.repository.trim().is_empty() || self.branch.trim().is_empty() {
            anyhow::bail!("local state repository and branch are required");
        }
        validate_repository_safety(&self.repository)?;
        validate_device(&self.device)
    }
}

pub fn validate_device(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
    {
        anyhow::bail!("device must use 1-64 letters, numbers, '.', '-', or '_': {value}");
    }
    Ok(())
}

pub fn normalize_repository(value: &str) -> Result<String> {
    let value = value.trim();
    if value.is_empty() {
        anyhow::bail!("repository is required");
    }
    validate_repository_safety(value)?;
    if std::path::Path::new(value).exists() {
        if std::env::var("CODEX_SYNC_ALLOW_LOCAL_REPOSITORY")
            .ok()
            .as_deref()
            == Some("1")
        {
            return Ok(value.to_owned());
        }
        anyhow::bail!(
            "local repository paths require CODEX_SYNC_ALLOW_LOCAL_REPOSITORY=1 for offline tests"
        );
    }
    if let Some((owner, name)) = value.split_once('/') {
        let looks_like_owner_name = !owner.contains(':')
            && !owner.contains('.')
            && !name.contains('/')
            && !name.contains('\\')
            && !name.is_empty();
        if looks_like_owner_name && !value.contains("://") {
            let owner = validate_repository_segment(owner, "owner")?;
            let name = validate_repository_segment(name.trim_end_matches(".git"), "name")?;
            return Ok(format!("https://github.com/{owner}/{name}.git"));
        }
    }
    if value.starts_with("https://") || value.starts_with("git@") || value.starts_with("ssh://") {
        return Ok(value.to_owned());
    }
    if value.contains('@')
        && value
            .split_once(':')
            .is_some_and(|(_, path)| !path.is_empty())
    {
        return Ok(value.to_owned());
    }
    anyhow::bail!("repository must be owner/name or a complete HTTPS/SSH Git URL")
}

/// Validate security-sensitive parts of a repository reference without
/// echoing the reference itself. Local paths are allowed here because the
/// offline test gate is enforced by [`normalize_repository`].
pub fn validate_repository_safety(value: &str) -> Result<()> {
    if value
        .chars()
        .any(|ch| ch.is_control() || ch.is_whitespace())
    {
        anyhow::bail!("repository reference contains whitespace or control characters");
    }
    if value.starts_with("http://") {
        anyhow::bail!("repository must use HTTPS or SSH; HTTP is not allowed");
    }
    if value.starts_with("https://") {
        if repository_authority(value, "https://").is_some_and(|authority| authority.contains('@'))
        {
            anyhow::bail!("repository URL must not contain embedded credentials");
        }
        return Ok(());
    }
    if value.starts_with("ssh://") {
        let authority = repository_authority(value, "ssh://").unwrap_or_default();
        if let Some(at) = authority.rfind('@') {
            if authority[..at].contains(':') {
                anyhow::bail!("repository SSH URL must not contain a password");
            }
        }
        return Ok(());
    }
    Ok(())
}

fn repository_authority<'a>(value: &'a str, scheme: &str) -> Option<&'a str> {
    value
        .strip_prefix(scheme)
        .and_then(|rest| rest.split(['/', '?', '#']).next())
}

fn validate_repository_segment(value: &str, label: &str) -> Result<String> {
    if value.is_empty() || value == "." || value == ".." || value.contains(['/', '\\', ':', '@']) {
        anyhow::bail!("invalid repository {label}: {value}");
    }
    Ok(value.to_owned())
}

#[derive(Debug, Clone)]
pub struct Paths {
    pub data_home: PathBuf,
    pub state_file: PathBuf,
    pub lock_file: PathBuf,
    pub cache: PathBuf,
    pub backup: PathBuf,
    pub codex_home: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Marketplace {
    pub name: String,
    pub url: String,
    #[serde(default = "default_branch")]
    pub git_ref: String,
    #[serde(default)]
    pub sparse: Vec<String>,
}

fn default_branch() -> String {
    "main".to_owned()
}

impl Marketplace {
    pub fn validate(&self) -> Result<()> {
        if !portable_name(&self.name) {
            anyhow::bail!("invalid marketplace name: {}", self.name);
        }
        let scp = self.url.split_once('@').is_some_and(|(user, rest)| {
            !user.is_empty()
                && !user.contains(':')
                && rest
                    .split_once(':')
                    .is_some_and(|(_, path)| !path.is_empty())
        });
        if !(self.url.starts_with("https://") || self.url.starts_with("ssh://") || scp) {
            anyhow::bail!("marketplace {} must use a Git source URL", self.name);
        }
        validate_git_ref(&self.git_ref)?;
        validate_sparse(&self.sparse)?;
        if self.url.contains('@')
            && (self.url.starts_with("http://") || self.url.starts_with("https://"))
        {
            anyhow::bail!("marketplace {} URL has embedded credentials", self.name);
        }
        if self.url.starts_with("ssh://") {
            let authority = self
                .url
                .strip_prefix("ssh://")
                .and_then(|rest| rest.split(['/', '?', '#']).next())
                .unwrap_or_default();
            if authority
                .rfind('@')
                .is_some_and(|at| authority[..at].contains(':'))
            {
                anyhow::bail!("marketplace {} URL has embedded credentials", self.name);
            }
        }
        if !self.url.starts_with("https://")
            && self
                .url
                .split_once('@')
                .is_some_and(|(user, _)| user.contains(':'))
        {
            anyhow::bail!("marketplace {} URL has embedded credentials", self.name);
        }
        Ok(())
    }
}

pub fn validate_git_ref(value: &str) -> Result<()> {
    if value.is_empty()
        || value.starts_with('-')
        || value.starts_with('.')
        || value.ends_with('.')
        || value == "@"
        || value.ends_with('/')
        || value.contains("..")
        || value.contains("@{")
        || value.contains([' ', '\t', '\r', '\n', '~', '^', ':', '?', '*', '[', '\\'])
        || value.chars().any(char::is_control)
        || value
            .split('/')
            .any(|part| part.is_empty() || part.starts_with('.') || part.ends_with(".lock"))
    {
        anyhow::bail!("invalid Git ref: {value}");
    }
    Ok(())
}

pub fn validate_sparse(paths: &[String]) -> Result<()> {
    for value in paths {
        if value.is_empty()
            || value.starts_with('-')
            || value.starts_with('/')
            || value.contains(['\\', '\0', '\r', '\n', '\t'])
            || value.contains("//")
            || value.chars().any(char::is_control)
        {
            anyhow::bail!("invalid marketplace sparse path: {value}");
        }
        let path = std::path::Path::new(value);
        if path.is_absolute()
            || path.components().any(|part| {
                matches!(
                    part,
                    std::path::Component::ParentDir | std::path::Component::CurDir
                )
            })
            || path.components().any(|part| part.as_os_str() == ".git")
        {
            anyhow::bail!("invalid marketplace sparse path: {value}");
        }
    }
    Ok(())
}

pub fn portable_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.split('-').all(|part| {
            !part.is_empty()
                && part
                    .chars()
                    .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit())
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repository_inputs_reject_http_and_local_without_offline_gate() {
        assert!(normalize_repository("http://example.test/repo.git").is_err());
        let temporary = tempfile::tempdir().unwrap();
        assert!(normalize_repository(temporary.path().to_str().unwrap()).is_err());
        assert!(normalize_repository("ssh://git@example.test/repo.git").is_ok());
        assert!(normalize_repository("alice@example.test:repo.git").is_ok());
    }

    #[test]
    fn ssh_passwords_are_rejected_without_echoing_the_secret() {
        for value in [
            "ssh://user:secret@example.test/repo.git",
            "ssh://user:@example.test/repo.git",
        ] {
            let error = normalize_repository(value).unwrap_err().to_string();
            assert!(error.contains("password"));
            assert!(!error.contains("secret"));
            assert!(!error.contains(value));
        }
        assert!(normalize_repository("ssh://git@example.test/repo.git").is_ok());
        assert!(normalize_repository("git@example.test:repo.git").is_ok());
    }

    #[test]
    fn refs_and_sparse_paths_reject_option_injection_and_traversal() {
        assert!(validate_git_ref("--upload-pack=bad").is_err());
        assert!(validate_git_ref("feature/new").is_ok());
        assert!(validate_sparse(&["--delete".to_owned()]).is_err());
        assert!(validate_sparse(&["../outside".to_owned()]).is_err());
        assert!(validate_sparse(&["marketplace/plugins".to_owned()]).is_ok());
    }

    #[test]
    fn marketplace_urls_accept_scp_and_reject_ssh_passwords() {
        let accepted = Marketplace {
            name: "market".to_owned(),
            url: "deploy@example.test:plugins.git".to_owned(),
            git_ref: "main".to_owned(),
            sparse: Vec::new(),
        };
        assert!(accepted.validate().is_ok());
        for url in [
            "ssh://deploy:secret@example.test/plugins.git",
            "deploy:secret@example.test:plugins.git",
        ] {
            let market = Marketplace {
                url: url.to_owned(),
                ..accepted.clone()
            };
            let error = market.validate().unwrap_err().to_string();
            assert!(error.contains("credentials") || error.contains("Git source"));
            assert!(!error.contains("secret"));
        }
    }
}
