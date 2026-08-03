use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use anyhow::{Context, Result};
use sha2::{Digest, Sha256};

use crate::storage::{open_directory_for_sync, sync_directory_on_disk, sync_open_directory};

#[derive(Debug, Clone)]
pub struct Artifact {
    pub root: PathBuf,
    pub digest: String,
}

fn reject_special(metadata: &fs::Metadata, path: &Path) -> Result<()> {
    let kind = metadata.file_type();
    if kind.is_symlink() || (!kind.is_dir() && !kind.is_file()) {
        anyhow::bail!(
            "provision artifact contains unsupported file type: {}",
            path.display()
        );
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            anyhow::bail!(
                "provision artifact contains a reparse point: {}",
                path.display()
            );
        }
    }
    Ok(())
}

fn collect(root: &Path, current: &Path, files: &mut Vec<(String, Vec<u8>)>) -> Result<()> {
    let metadata = fs::symlink_metadata(current)?;
    reject_special(&metadata, current)?;
    if metadata.is_file() {
        let relative = current
            .strip_prefix(root)?
            .to_str()
            .context("provision artifact path is not valid UTF-8")?
            .replace('\\', "/");
        if relative.is_empty()
            || relative
                .split('/')
                .any(|part| part.is_empty() || part == "." || part == "..")
        {
            anyhow::bail!("invalid provision artifact relative path: {relative}");
        }
        files.push((relative, fs::read(current)?));
        return Ok(());
    }
    for entry in fs::read_dir(current)? {
        let entry = entry?;
        collect(root, &entry.path(), files)?;
    }
    Ok(())
}

fn tree_digest(root: &Path) -> Result<String> {
    let mut files = Vec::new();
    collect(root, root, &mut files)?;
    files.sort_by(|a, b| a.0.cmp(&b.0));
    let mut digest = Sha256::new();
    for (path, bytes) in files {
        digest.update(path.as_bytes());
        digest.update([0]);
        digest.update(bytes);
        digest.update([0]);
    }
    Ok(hex::encode(digest.finalize()))
}

pub fn digest(root: &Path) -> Result<String> {
    tree_digest(root)
}

fn copy_regular_with<F>(source: &Path, destination: &Path, sync_directory: &mut F) -> Result<()>
where
    F: FnMut(&Path) -> Result<()>,
{
    let metadata = fs::symlink_metadata(source)?;
    reject_special(&metadata, source)?;
    if metadata.is_dir() {
        fs::create_dir_all(destination)?;
        for entry in fs::read_dir(source)? {
            let entry = entry?;
            copy_regular_with(
                &entry.path(),
                &destination.join(entry.file_name()),
                sync_directory,
            )?;
        }
        // Directory entries become durable only after all children have been
        // copied and synced. This post-order call is what makes publication
        // safe for arbitrarily nested artifact trees.
        sync_directory(destination)?;
    } else {
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::copy(source, destination)?;
        #[cfg(windows)]
        fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(destination)?
            .sync_all()?;
        #[cfg(not(windows))]
        fs::File::open(destination)?.sync_all()?;
    }
    Ok(())
}

fn mark_read_only(root: &Path) -> Result<()> {
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        let metadata = fs::symlink_metadata(&path)?;
        reject_special(&metadata, &path)?;
        if metadata.is_dir() {
            mark_read_only(&path)?;
        } else {
            let mut permissions = metadata.permissions();
            permissions.set_readonly(true);
            fs::set_permissions(&path, permissions)?;
        }
    }
    let mut permissions = fs::metadata(root)?.permissions();
    permissions.set_readonly(true);
    fs::set_permissions(root, permissions)?;
    Ok(())
}

fn materialize_with_sync<F>(
    source: &Path,
    data_home: &Path,
    mut sync_directory: F,
) -> Result<Artifact>
where
    F: FnMut(&Path) -> Result<()>,
{
    reject_special(&fs::symlink_metadata(source)?, source)?;
    let source =
        fs::canonicalize(source).with_context(|| format!("resolve {}", source.display()))?;
    let digest = tree_digest(&source)?;
    let objects = data_home.join("provision-artifacts");
    let destination = objects.join(&digest);
    if destination.is_dir() {
        if tree_digest(&destination)? != digest {
            anyhow::bail!(
                "provision artifact digest mismatch: {}",
                destination.display()
            );
        }
        mark_read_only(&destination)?;
        return Ok(Artifact {
            root: destination,
            digest,
        });
    }
    fs::create_dir_all(&objects)?;
    static STAGING_COUNTER: AtomicU64 = AtomicU64::new(0);
    let staging = objects.join(format!(
        ".staging-{}-{}",
        std::process::id(),
        STAGING_COUNTER.fetch_add(1, Ordering::Relaxed)
    ));
    copy_regular_with(&source, &staging, &mut sync_directory)?;
    if tree_digest(&staging)? != digest {
        fs::remove_dir_all(&staging)?;
        anyhow::bail!("staged provision artifact digest mismatch");
    }
    // Persist the staging directory entry before publishing it. All entries
    // below the staging root have already been synced in post-order above.
    sync_directory(&objects)?;
    let objects_directory = open_directory_for_sync(&objects)?;
    fs::rename(&staging, &destination)?;
    // The rename is atomic, but its directory entry is not durable until the
    // containing objects directory is synced successfully.
    sync_open_directory(&objects_directory, &destination)?;
    if tree_digest(&destination)? != digest {
        anyhow::bail!("published provision artifact digest mismatch");
    }
    mark_read_only(&destination)?;
    Ok(Artifact {
        root: destination,
        digest,
    })
}

pub fn materialize(source: &Path, data_home: &Path) -> Result<Artifact> {
    materialize_with_sync(source, data_home, sync_directory_on_disk)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_survives_source_replacement() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        fs::create_dir_all(&source).unwrap();
        fs::write(source.join("script.sh"), "old").unwrap();
        let artifact = materialize(&source, temp.path()).unwrap();
        fs::write(source.join("script.sh"), "new").unwrap();
        assert_eq!(
            fs::read_to_string(artifact.root.join("script.sh")).unwrap(),
            "old"
        );
        let changed = materialize(&source, temp.path()).unwrap();
        assert_ne!(artifact.digest, changed.digest);
    }

    #[cfg(unix)]
    #[test]
    fn read_only_source_file_is_materialized() {
        use std::os::unix::fs::PermissionsExt;

        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        let script = source.join("script.sh");
        fs::create_dir_all(&source).unwrap();
        fs::write(&script, "read-only").unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o444)).unwrap();

        let artifact = materialize(&source, temp.path()).unwrap();
        assert_eq!(
            fs::read_to_string(artifact.root.join("script.sh")).unwrap(),
            "read-only"
        );
    }

    #[test]
    fn nested_tree_is_synced_post_order_and_published_intact() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        let nested = source.join("nested").join("deeper");
        fs::create_dir_all(&nested).unwrap();
        fs::write(nested.join("script.sh"), "nested").unwrap();

        let synced = std::cell::RefCell::new(Vec::new());
        let artifact = materialize_with_sync(&source, temp.path(), |path| {
            sync_directory_on_disk(path)?;
            synced.borrow_mut().push(path.to_path_buf());
            Ok(())
        })
        .unwrap();

        assert_eq!(
            fs::read_to_string(artifact.root.join("nested/deeper/script.sh")).unwrap(),
            "nested"
        );
        let synced = synced.into_inner();
        let staging_deeper = synced
            .iter()
            .position(|path| path.ends_with("nested/deeper"))
            .unwrap();
        let staging_root = synced
            .iter()
            .position(|path| {
                path.file_name()
                    .is_some_and(|name| name.to_string_lossy().starts_with(".staging-"))
            })
            .unwrap();
        assert!(staging_deeper < staging_root);
    }

    #[test]
    fn nested_directory_sync_failure_prevents_publish() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        let nested = source.join("nested");
        fs::create_dir_all(&nested).unwrap();
        fs::write(nested.join("script.sh"), "nested").unwrap();
        let digest = digest(&source).unwrap();

        let error = materialize_with_sync(&source, temp.path(), |path| {
            if path.file_name().is_some_and(|name| name == "nested") {
                anyhow::bail!("injected nested directory sync failure");
            }
            Ok(())
        })
        .unwrap_err();

        assert!(error
            .to_string()
            .contains("injected nested directory sync failure"));
        assert!(!temp
            .path()
            .join("provision-artifacts")
            .join(digest)
            .exists());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_is_rejected() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("source");
        fs::create_dir_all(&source).unwrap();
        std::os::unix::fs::symlink("missing", source.join("link")).unwrap();
        assert!(materialize(&source, temp.path()).is_err());
    }
}
