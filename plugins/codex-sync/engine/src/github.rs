use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use reqwest::blocking::Client;
use reqwest::header::{ACCEPT, AUTHORIZATION, USER_AGENT};
use serde::Deserialize;
use serde_json::json;
use zip::ZipArchive;

use crate::model::RepositoryRef;
use crate::storage::replace_tree_atomically;

const API_VERSION: &str = "2022-11-28";
const MAX_ARCHIVE_BYTES: u64 = 128 * 1024 * 1024;
const MAX_EXTRACTED_BYTES: u64 = 512 * 1024 * 1024;
const MAX_ARCHIVE_FILES: usize = 20_000;
const MAX_SINGLE_FILE_BYTES: u64 = 128 * 1024 * 1024;

#[derive(Debug, Deserialize)]
struct CommitResponse {
    sha: String,
}

#[derive(Debug, Deserialize)]
struct RefResponse {
    object: GitObject,
}

#[derive(Debug, Deserialize)]
struct GitObject {
    sha: String,
}

#[derive(Debug, Deserialize)]
struct GitCommitResponse {
    sha: String,
    tree: GitObject,
}

#[derive(Debug, Deserialize)]
struct GitTreeResponse {
    tree: Vec<GitTreeEntry>,
}

#[derive(Debug, Deserialize)]
struct GitTreeEntry {
    path: String,
    #[serde(rename = "type")]
    object_type: String,
}

pub fn http_client() -> Result<Client> {
    Client::builder()
        .redirect(reqwest::redirect::Policy::limited(5))
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .context("build HTTP client")
}

pub struct GithubClient {
    client: Client,
    token: String,
    api_base: String,
}

impl GithubClient {
    pub fn new(client: Client, token: String) -> Result<Self> {
        let api_base = std::env::var("CODEX_SYNC_GITHUB_API_URL")
            .unwrap_or_else(|_| "https://api.github.com".to_owned());
        let value = Self {
            client,
            token,
            api_base: api_base.trim_end_matches('/').to_owned(),
        };
        value.headers()?;
        Ok(value)
    }

    pub fn resolve_commit(&self, repository: &RepositoryRef) -> Result<String> {
        let url = format!(
            "{}/repos/{}/{}/commits/{}",
            self.api_base,
            urlencoding::encode(&repository.owner),
            urlencoding::encode(&repository.name),
            urlencoding::encode(&repository.git_ref)
        );
        let response: CommitResponse = self
            .request(&url)
            .send()
            .with_context(|| format!("resolve {} at {}", repository.slug(), repository.git_ref))?
            .error_for_status()
            .with_context(|| format!("GitHub cannot access {}", repository.slug()))?
            .json()
            .context("parse GitHub commit response")?;
        Ok(response.sha)
    }

    pub fn download_repository(
        &self,
        repository: &RepositoryRef,
        commit: &str,
        destination: &Path,
    ) -> Result<()> {
        let url = format!(
            "{}/repos/{}/{}/zipball/{}",
            self.api_base,
            urlencoding::encode(&repository.owner),
            urlencoding::encode(&repository.name),
            urlencoding::encode(commit)
        );
        let mut response = self
            .request(&url)
            .send()
            .with_context(|| format!("download {} at {commit}", repository.slug()))?
            .error_for_status()
            .with_context(|| format!("GitHub archive download failed for {}", repository.slug()))?;
        if response
            .content_length()
            .is_some_and(|length| length > MAX_ARCHIVE_BYTES)
        {
            anyhow::bail!(
                "GitHub archive exceeds the {} byte limit",
                MAX_ARCHIVE_BYTES
            );
        }
        let mut bytes = Vec::new();
        response
            .by_ref()
            .take(MAX_ARCHIVE_BYTES + 1)
            .read_to_end(&mut bytes)
            .context("read GitHub archive")?;
        if bytes.len() as u64 > MAX_ARCHIVE_BYTES {
            anyhow::bail!(
                "GitHub archive exceeds the {} byte limit",
                MAX_ARCHIVE_BYTES
            );
        }
        let parent = destination
            .parent()
            .context("repository snapshot destination has no parent")?;
        fs::create_dir_all(parent)?;
        let temporary =
            tempfile::tempdir_in(parent).context("create archive extraction directory")?;
        let staged = temporary.path().join("snapshot");
        fs::create_dir(&staged)?;
        extract_zip_safely(&bytes, &staged)?;
        replace_tree_atomically(&staged, destination)
    }

    pub fn publish_repository(
        &self,
        repository: &RepositoryRef,
        expected_base: &str,
        source: &Path,
        message: &str,
    ) -> Result<String> {
        let repository_url = format!(
            "{}/repos/{}/{}",
            self.api_base,
            urlencoding::encode(&repository.owner),
            urlencoding::encode(&repository.name)
        );
        let ref_url = format!(
            "{}/git/ref/heads/{}",
            repository_url,
            urlencoding::encode(&repository.git_ref)
        );
        let current_ref: RefResponse = self
            .request(&ref_url)
            .send()?
            .error_for_status()
            .context("read GitHub branch reference")?
            .json()?;
        if current_ref.object.sha != expected_base {
            anyhow::bail!(
                "remote branch advanced from {} to {}; synchronize before publishing",
                expected_base,
                current_ref.object.sha
            );
        }

        let base_commit_url = format!("{}/git/commits/{}", repository_url, expected_base);
        let base_commit: GitCommitResponse = self
            .request(&base_commit_url)
            .send()?
            .error_for_status()
            .context("read GitHub base commit")?
            .json()?;
        let remote_paths = self.remote_blob_paths(&repository_url, &base_commit.tree.sha)?;
        let local_files = collect_files(source)?;
        let mut tree_entries = Vec::new();
        for (path, bytes) in &local_files {
            let content = std::str::from_utf8(bytes)
                .with_context(|| format!("publish only supports UTF-8 files: {path}"))?;
            let blob: GitObject = self
                .client
                .post(format!("{repository_url}/git/blobs"))
                .headers(self.headers()?)
                .json(&json!({"content": content, "encoding": "utf-8"}))
                .send()?
                .error_for_status()
                .with_context(|| format!("create GitHub blob for {path}"))?
                .json()?;
            tree_entries.push(json!({
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob.sha
            }));
        }
        let local_paths: BTreeSet<_> = local_files.keys().cloned().collect();
        for remote_path in remote_paths.difference(&local_paths) {
            tree_entries.push(json!({
                "path": remote_path,
                "mode": "100644",
                "type": "blob",
                "sha": null
            }));
        }
        let new_tree: GitObject = self
            .client
            .post(format!("{repository_url}/git/trees"))
            .headers(self.headers()?)
            .json(&json!({"base_tree": base_commit.tree.sha, "tree": tree_entries}))
            .send()?
            .error_for_status()
            .context("create GitHub tree")?
            .json()?;
        let new_commit: GitCommitResponse = self
            .client
            .post(format!("{repository_url}/git/commits"))
            .headers(self.headers()?)
            .json(&json!({
                "message": message,
                "tree": new_tree.sha,
                "parents": [base_commit.sha]
            }))
            .send()?
            .error_for_status()
            .context("create GitHub commit")?
            .json()?;
        self.client
            .patch(ref_url.replace("/git/ref/", "/git/refs/"))
            .headers(self.headers()?)
            .json(&json!({"sha": new_commit.sha, "force": false}))
            .send()?
            .error_for_status()
            .context("advance GitHub branch; the remote branch may have changed")?;
        Ok(new_commit.sha)
    }

    fn remote_blob_paths(&self, repository_url: &str, tree_sha: &str) -> Result<BTreeSet<String>> {
        let url = format!("{repository_url}/git/trees/{tree_sha}?recursive=1");
        let tree: GitTreeResponse = self
            .request(&url)
            .send()?
            .error_for_status()
            .context("read recursive GitHub tree")?
            .json()?;
        Ok(tree
            .tree
            .into_iter()
            .filter(|entry| entry.object_type == "blob")
            .map(|entry| entry.path)
            .collect())
    }

    fn request(&self, url: &str) -> reqwest::blocking::RequestBuilder {
        self.client.get(url).headers(
            self.headers()
                .expect("GitHub token was validated when the client was created"),
        )
    }

    fn headers(&self) -> Result<reqwest::header::HeaderMap> {
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert(USER_AGENT, "codex-sync/0.1".parse()?);
        headers.insert(ACCEPT, "application/vnd.github+json".parse()?);
        headers.insert("X-GitHub-Api-Version", API_VERSION.parse()?);
        headers.insert(
            AUTHORIZATION,
            format!("Bearer {}", self.token)
                .parse()
                .context("GitHub token contains invalid header characters")?,
        );
        Ok(headers)
    }
}

fn collect_files(root: &Path) -> Result<BTreeMap<String, Vec<u8>>> {
    let mut output = BTreeMap::new();
    collect_files_recursive(root, root, &mut output)?;
    Ok(output)
}

fn collect_files_recursive(
    root: &Path,
    directory: &Path,
    output: &mut BTreeMap<String, Vec<u8>>,
) -> Result<()> {
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_dir() {
            if entry.file_name() != ".git" {
                collect_files_recursive(root, &path, output)?;
            }
        } else if entry.file_type()?.is_file() {
            let relative = path
                .strip_prefix(root)?
                .to_string_lossy()
                .replace('\\', "/");
            output.insert(relative, fs::read(path)?);
        }
    }
    Ok(())
}

fn extract_zip_safely(bytes: &[u8], destination: &Path) -> Result<()> {
    let mut archive = ZipArchive::new(Cursor::new(bytes)).context("open GitHub ZIP archive")?;
    if archive.len() > MAX_ARCHIVE_FILES {
        anyhow::bail!("archive contains more than {MAX_ARCHIVE_FILES} entries");
    }
    let mut root_name: Option<PathBuf> = None;
    let mut declared_total = 0_u64;
    for index in 0..archive.len() {
        let file = archive.by_index(index).context("read ZIP entry")?;
        if file.size() > MAX_SINGLE_FILE_BYTES {
            anyhow::bail!("archive entry exceeds the per-file extraction limit");
        }
        declared_total = declared_total
            .checked_add(file.size())
            .context("archive size overflow")?;
        if declared_total > MAX_EXTRACTED_BYTES {
            anyhow::bail!("archive exceeds the total extraction limit");
        }
        let enclosed = file
            .enclosed_name()
            .context("archive contains an unsafe path")?
            .to_owned();
        if let Some(first) = enclosed.components().next() {
            let first = PathBuf::from(first.as_os_str());
            if let Some(expected) = &root_name {
                if expected != &first {
                    anyhow::bail!("archive contains multiple top-level directories");
                }
            } else {
                root_name = Some(first);
            }
        }
    }
    let mut extracted_total = 0_u64;
    for index in 0..archive.len() {
        let mut file = archive.by_index(index).context("read ZIP entry")?;
        let enclosed = file
            .enclosed_name()
            .context("archive contains an unsafe path")?;
        let relative: PathBuf = enclosed.components().skip(1).collect();
        if relative.as_os_str().is_empty() {
            continue;
        }
        let output = destination.join(relative);
        if file.is_dir() {
            fs::create_dir_all(&output).with_context(|| format!("create {}", output.display()))?;
        } else {
            let parent = output.parent().context("archive entry has no parent")?;
            fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
            let mut output_file = fs::File::create(&output)
                .with_context(|| format!("create {}", output.display()))?;
            let copied = std::io::copy(
                &mut file.by_ref().take(MAX_SINGLE_FILE_BYTES + 1),
                &mut output_file,
            )
            .with_context(|| format!("extract {}", output.display()))?;
            if copied > MAX_SINGLE_FILE_BYTES {
                anyhow::bail!("archive entry exceeds the per-file extraction limit");
            }
            extracted_total = extracted_total
                .checked_add(copied)
                .context("archive extraction size overflow")?;
            if extracted_total > MAX_EXTRACTED_BYTES {
                anyhow::bail!("archive exceeds the total extraction limit");
            }
            output_file.flush()?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use zip::write::SimpleFileOptions;

    #[test]
    fn extraction_strips_github_root_directory() {
        let mut buffer = Cursor::new(Vec::new());
        {
            let mut writer = zip::ZipWriter::new(&mut buffer);
            writer
                .start_file("owner-repo-sha/AGENTS.md", SimpleFileOptions::default())
                .unwrap();
            writer.write_all(b"rules").unwrap();
            writer.finish().unwrap();
        }
        let directory = tempfile::tempdir().unwrap();
        extract_zip_safely(buffer.get_ref(), directory.path()).unwrap();
        assert_eq!(
            fs::read_to_string(directory.path().join("AGENTS.md")).unwrap(),
            "rules"
        );
    }

    #[test]
    fn extraction_rejects_parent_traversal() {
        let mut buffer = Cursor::new(Vec::new());
        {
            let mut writer = zip::ZipWriter::new(&mut buffer);
            writer
                .start_file("../escape", SimpleFileOptions::default())
                .unwrap();
            writer.write_all(b"bad").unwrap();
            writer.finish().unwrap();
        }
        let directory = tempfile::tempdir().unwrap();
        assert!(extract_zip_safely(buffer.get_ref(), directory.path()).is_err());
    }
}
