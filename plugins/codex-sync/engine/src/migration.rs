use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

use crate::model::Marketplace;
use crate::storage::{atomic_write, copy_tree, read_toml, remove_if_exists};

/// Convert a v2 repository in-place to the fixed v3 layout.  The cache is
/// engine-owned, so removing the old manifest/files cannot affect user files.
pub fn migrate_repository(root: &Path) -> Result<bool> {
    let manifest_path = root.join("codex-sync.toml");
    if !manifest_path.exists() {
        anyhow::bail!("repository is missing codex-sync.toml");
    }
    let manifest: toml::Value = read_toml(&manifest_path)?;
    let table = manifest
        .as_table()
        .context("codex-sync.toml must be a table")?;
    let schema = table
        .get("schema_version")
        .and_then(toml::Value::as_integer)
        .context("codex-sync.toml schema_version is required")?;
    if schema == 3 {
        if table.len() != 1 {
            anyhow::bail!("v3 codex-sync.toml may contain only schema_version = 3");
        }
        return Ok(false);
    }
    if schema != 2 {
        anyhow::bail!("unsupported repository schema version {schema}; expected 3");
    }

    let agents = relative_path(table, "agents", "AGENTS.md")?;
    let profiles = relative_path(table, "agent_profiles", "agents")?;
    let common = relative_path(table, "common_config", "config/common.toml")?;
    let devices = relative_path(table, "devices", "devices")?;
    let markets = relative_path(table, "marketplaces", "marketplaces.toml")?;
    let plugins = relative_path(table, "plugins", "plugins.toml")?;
    let providers = relative_path(table, "providers", "providers.toml")?;
    for path in [
        &agents, &profiles, &common, &devices, &markets, &plugins, &providers,
    ] {
        validate_source_path(root, path)?;
    }

    let staged = tempfile::tempdir_in(root.parent().unwrap_or(root))?;
    let destination = staged.path().join("repository");
    fs::create_dir_all(&destination)?;
    copy_optional_file(root.join(&agents), destination.join("AGENTS.md"), b"")?;
    copy_optional_dir(root.join(&profiles), destination.join("agents"))?;
    copy_optional_dir(root.join(&devices), destination.join("devices"))?;

    let mut common_value = read_value_or_empty(&root.join(&common))?;
    if let Some(provider_value) = read_value(&root.join(&providers))? {
        let providers_root = provider_value
            .as_table()
            .context("providers.toml must be a table")?;
        let providers_table = providers_root
            .get("providers")
            .context("providers.toml requires a top-level providers table")?
            .as_table()
            .context("providers.toml providers must be a table")?
            .clone();
        if !providers_table.is_empty() {
            let common_table = common_value
                .as_table_mut()
                .context("common config must be a table")?;
            let models = common_table
                .entry("model_providers".to_owned())
                .or_insert_with(|| toml::Value::Table(toml::map::Map::new()));
            let models_table = models
                .as_table_mut()
                .context("model_providers must be a table")?;
            for (name, value) in providers_table {
                models_table.insert(name, value);
            }
        }
    }
    write_toml(&destination.join("config/common.toml"), &common_value)?;

    let marketplaces = migrate_markets(&root.join(&markets))?;
    write_toml(
        &destination.join("marketplaces.toml"),
        &toml::Value::Table({
            let mut table = toml::map::Map::new();
            table.insert("marketplaces".to_owned(), toml::Value::Array(marketplaces));
            table
        }),
    )?;

    let plugins_array = migrate_plugins(&root.join(&plugins))?;
    write_toml(
        &destination.join("plugins.toml"),
        &toml::Value::Table({
            let mut table = toml::map::Map::new();
            table.insert("plugins".to_owned(), toml::Value::Array(plugins_array));
            table
        }),
    )?;

    atomic_write(
        &destination.join("codex-sync.toml"),
        b"schema_version = 3\n",
    )?;
    replace_canonical_targets(
        root,
        &destination,
        [
            &agents, &profiles, &common, &devices, &markets, &plugins, &providers,
        ],
    )?;

    // Remove v2-only files that are not part of the fixed v3 contract.
    for path in [root.join(&providers), root.join("pending-plan.json")] {
        if path != root.join("config/common.toml") {
            remove_if_exists(&path)?;
        }
    }
    for path in [agents, profiles, common, devices, markets, plugins] {
        let old = root.join(path);
        let canonical = [
            "AGENTS.md",
            "agents",
            "config/common.toml",
            "devices",
            "marketplaces.toml",
            "plugins.toml",
        ]
        .iter()
        .any(|item| old == root.join(item));
        if !canonical {
            remove_if_exists(&old)?;
        }
    }
    Ok(true)
}

fn relative_path(
    table: &toml::map::Map<String, toml::Value>,
    key: &str,
    default: &str,
) -> Result<PathBuf> {
    let value = table
        .get(key)
        .and_then(toml::Value::as_str)
        .unwrap_or(default);
    let path = Path::new(value);
    if path.is_absolute()
        || value == "."
        || path.components().any(|part| {
            matches!(part, std::path::Component::ParentDir) || part.as_os_str() == ".git"
        })
        || value.is_empty()
    {
        anyhow::bail!("v2 manifest path must stay inside repository: {value}");
    }
    Ok(path.to_owned())
}

fn validate_source_path(root: &Path, relative: &Path) -> Result<()> {
    let mut current = root.to_path_buf();
    for component in relative.components() {
        let std::path::Component::Normal(name) = component else {
            continue;
        };
        if name == ".git" {
            anyhow::bail!(
                "v2 manifest path may not enter .git: {}",
                relative.display()
            );
        }
        current.push(name);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                anyhow::bail!("v2 manifest path contains a symlink: {}", current.display());
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
            Err(error) => {
                return Err(error).with_context(|| format!("inspect {}", current.display()))
            }
        }
    }
    Ok(())
}

fn read_value(path: &Path) -> Result<Option<toml::Value>> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error).with_context(|| format!("inspect {}", path.display())),
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("v2 manifest path is a symlink: {}", path.display());
        }
        Ok(_) => Ok(Some(read_toml(path)?)),
    }
}

fn read_value_or_empty(path: &Path) -> Result<toml::Value> {
    Ok(read_value(path)?.unwrap_or_else(|| toml::Value::Table(toml::map::Map::new())))
}

fn copy_optional_file(source: PathBuf, destination: PathBuf, default: &[u8]) -> Result<()> {
    let bytes = match fs::read(&source) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => default.to_vec(),
        Err(error) => return Err(error).with_context(|| format!("read {}", source.display())),
    };
    atomic_write(&destination, &bytes)
}

fn copy_optional_dir(source: PathBuf, destination: PathBuf) -> Result<()> {
    match fs::symlink_metadata(&source) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!("v2 manifest path is a symlink: {}", source.display());
        }
        Ok(metadata) if metadata.file_type().is_dir() => copy_tree(&source, &destination),
        Ok(_) => anyhow::bail!(
            "v2 manifest directory is not a directory: {}",
            source.display()
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir_all(destination).map_err(Into::into)
        }
        Err(error) => Err(error).with_context(|| format!("inspect {}", source.display())),
    }
}

fn replace_canonical_targets<const N: usize>(
    root: &Path,
    staged: &Path,
    old_paths: [&PathBuf; N],
) -> Result<()> {
    remove_if_exists(&root.join("codex-sync.toml"))?;
    remove_if_exists(&root.join("AGENTS.md"))?;
    remove_if_exists(&root.join("config/common.toml"))?;
    remove_if_exists(&root.join("marketplaces.toml"))?;
    remove_if_exists(&root.join("plugins.toml"))?;
    remove_managed_tomls(&root.join("agents"))?;
    remove_managed_tomls(&root.join("devices"))?;
    for relative in old_paths {
        if ![
            Path::new("AGENTS.md"),
            Path::new("agents"),
            Path::new("config/common.toml"),
            Path::new("devices"),
            Path::new("marketplaces.toml"),
            Path::new("plugins.toml"),
        ]
        .contains(&relative.as_path())
        {
            remove_if_exists(&root.join(relative))?;
        }
    }
    for entry in fs::read_dir(staged)? {
        let entry = entry?;
        let source = entry.path();
        let destination = root.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            fs::create_dir_all(&destination)?;
            copy_directory_contents(&source, &destination)?;
        } else {
            atomic_write(&destination, &fs::read(&source)?)?;
        }
    }
    Ok(())
}

fn copy_directory_contents(source: &Path, destination: &Path) -> Result<()> {
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            fs::create_dir_all(&destination_path)?;
            copy_directory_contents(&source_path, &destination_path)?;
        } else if entry.file_type()?.is_file() {
            atomic_write(&destination_path, &fs::read(&source_path)?)?;
        } else {
            anyhow::bail!("refusing to copy special file {}", source_path.display());
        }
    }
    Ok(())
}

fn remove_managed_tomls(directory: &Path) -> Result<()> {
    match fs::symlink_metadata(directory) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(error).with_context(|| format!("inspect {}", directory.display()))
        }
        Ok(metadata) if metadata.file_type().is_symlink() => {
            anyhow::bail!(
                "managed TOML directory is a symlink: {}",
                directory.display()
            )
        }
        Ok(metadata) if !metadata.file_type().is_dir() => {
            anyhow::bail!(
                "managed TOML path is not a directory: {}",
                directory.display()
            )
        }
        Ok(_) => {}
    }
    let entries =
        fs::read_dir(directory).with_context(|| format!("read {}", directory.display()))?;
    for entry in entries {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_file()
            && path.extension().and_then(|value| value.to_str()) == Some("toml")
        {
            remove_if_exists(&path)?;
        }
    }
    Ok(())
}

fn write_toml(path: &Path, value: &toml::Value) -> Result<()> {
    atomic_write(path, toml::to_string_pretty(value)?.as_bytes())
}

fn migrate_markets(path: &Path) -> Result<Vec<toml::Value>> {
    let Some(value) = read_value(path)? else {
        return Ok(Vec::new());
    };
    let root = value
        .as_table()
        .context("marketplaces.toml must be a table")?;
    let list = root
        .get("marketplaces")
        .context("marketplaces.toml requires a top-level marketplaces array")?
        .as_array()
        .context("marketplaces.toml marketplaces must be an array")?
        .clone();
    let mut result = Vec::new();
    for item in list {
        let table = item
            .as_table()
            .context("marketplace entry must be a table")?;
        let source = table
            .get("source")
            .and_then(toml::Value::as_str)
            .unwrap_or("git");
        if source == "github-snapshot" {
            anyhow::bail!(
                "github-snapshot marketplaces are no longer supported; migrate to a Git source"
            );
        }
        if source != "git" {
            anyhow::bail!("marketplace source must be git");
        }
        let name = table
            .get("name")
            .and_then(toml::Value::as_str)
            .context("marketplace name missing")?;
        let url = table
            .get("url")
            .and_then(toml::Value::as_str)
            .context("marketplace URL missing")?;
        let sparse = table
            .get("sparse")
            .map(|value| {
                value
                    .as_array()
                    .context("marketplace sparse must be an array")?
                    .iter()
                    .map(|item| {
                        item.as_str()
                            .map(str::to_owned)
                            .context("marketplace sparse entries must be strings")
                    })
                    .collect::<Result<Vec<_>>>()
            })
            .transpose()?
            .unwrap_or_default();
        let git_ref = table
            .get("git_ref")
            .and_then(toml::Value::as_str)
            .unwrap_or("main")
            .to_owned();
        let mut output = toml::map::Map::new();
        output.insert("source".to_owned(), toml::Value::String("git".to_owned()));
        output.insert("name".to_owned(), toml::Value::String(name.to_owned()));
        output.insert("url".to_owned(), toml::Value::String(url.to_owned()));
        output.insert("git_ref".to_owned(), toml::Value::String(git_ref.clone()));
        output.insert(
            "sparse".to_owned(),
            toml::Value::Array(sparse.iter().cloned().map(toml::Value::String).collect()),
        );
        Marketplace {
            name: name.to_owned(),
            url: url.to_owned(),
            git_ref,
            sparse,
        }
        .validate()?;
        result.push(toml::Value::Table(output));
    }
    Ok(result)
}

fn migrate_plugins(path: &Path) -> Result<Vec<toml::Value>> {
    let Some(value) = read_value(path)? else {
        return Ok(Vec::new());
    };
    let root = value.as_table().context("plugins.toml must be a table")?;
    let list = root
        .get("plugins")
        .context("plugins.toml requires a top-level plugins array")?
        .as_array()
        .context("plugins.toml plugins must be an array")?
        .clone();
    let mut result = Vec::new();
    for item in list {
        let (id, enabled) = if let Some(text) = item.as_str() {
            (text.to_owned(), true)
        } else {
            let table = item
                .as_table()
                .context("plugin entry must be a string or table")?;
            (
                table
                    .get("id")
                    .and_then(toml::Value::as_str)
                    .context("plugin id missing")?
                    .to_owned(),
                table
                    .get("enabled")
                    .and_then(toml::Value::as_bool)
                    .unwrap_or(true),
            )
        };
        if enabled {
            result.push(toml::Value::String(id));
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn custom_paths_cannot_enter_git() {
        let mut table = toml::map::Map::new();
        table.insert(
            "agents".into(),
            toml::Value::String(".git/AGENTS.md".into()),
        );
        assert!(relative_path(&table, "agents", "AGENTS.md").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_component_is_rejected() {
        use std::os::unix::fs::symlink;
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("repo");
        let outside = temporary.path().join("outside");
        fs::create_dir_all(&root).unwrap();
        fs::create_dir_all(&outside).unwrap();
        symlink(&outside, root.join("linked")).unwrap();
        fs::write(
            root.join("codex-sync.toml"),
            "schema_version = 2\nagents = \"linked/AGENTS.md\"\n",
        )
        .unwrap();
        assert!(migrate_repository(&root).is_err());
    }

    #[test]
    fn v2_container_files_are_strict_before_replacement() {
        let cases = [
            ("providers.toml", "provider = {}\n"),
            ("providers.toml", "providers = []\n"),
            ("marketplaces.toml", "market = []\n"),
            ("marketplaces.toml", "marketplaces = {}\n"),
            ("plugins.toml", "plugin = []\n"),
            ("plugins.toml", "plugins = {}\n"),
        ];
        for (file, contents) in cases {
            let temporary = tempfile::tempdir().unwrap();
            let root = temporary.path().join("repo");
            fs::create_dir_all(&root).unwrap();
            let manifest = "schema_version = 2\n";
            fs::write(root.join("codex-sync.toml"), manifest).unwrap();
            fs::write(root.join(file), contents).unwrap();
            assert!(
                migrate_repository(&root).is_err(),
                "case {file}: {contents}"
            );
            assert_eq!(
                fs::read_to_string(root.join("codex-sync.toml")).unwrap(),
                manifest
            );
        }
    }

    #[test]
    fn v2_missing_container_files_are_empty_and_migrate() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("repo");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("codex-sync.toml"), "schema_version = 2\n").unwrap();
        assert!(migrate_repository(&root).unwrap());
        assert_eq!(
            fs::read_to_string(root.join("codex-sync.toml")).unwrap(),
            "schema_version = 3\n"
        );
        assert_eq!(
            fs::read_to_string(root.join("marketplaces.toml")).unwrap(),
            "marketplaces = []\n"
        );
        assert_eq!(
            fs::read_to_string(root.join("plugins.toml")).unwrap(),
            "plugins = []\n"
        );
    }
}
