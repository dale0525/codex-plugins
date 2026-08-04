use std::fs;
use std::path::Path;

use crate::app::{create_core_backup, restore_core_backup};
use crate::model::Paths;

fn backup_test_paths(root: &Path) -> Paths {
    let codex_home = root.join("codex");
    let data_home = root.join("sync");
    Paths {
        data_home: data_home.clone(),
        state_file: data_home.join("state.toml"),
        lock_file: data_home.join("sync.lock"),
        cache: data_home.join("git-cache"),
        backup: data_home.join("backup/previous"),
        codex_home,
    }
}

#[test]
fn core_backup_round_trip_restores_files_and_absent_markers() {
    let temporary = tempfile::tempdir().unwrap();
    let codex_home = temporary.path().join("codex");
    let data_home = temporary.path().join("sync");
    fs::create_dir_all(codex_home.join("agents")).unwrap();
    fs::write(codex_home.join("config.toml"), "model = \"before\"\n").unwrap();
    fs::write(codex_home.join("AGENTS.md"), "before\n").unwrap();
    fs::write(
        codex_home.join("agents/default.toml"),
        "name = \"default\"\n",
    )
    .unwrap();
    let cache = data_home.join("git-cache");
    fs::create_dir_all(cache.join("agents")).unwrap();
    fs::write(cache.join("AGENTS.md"), "remote\n").unwrap();
    fs::write(cache.join("agents/default.toml"), "name = \"default\"\n").unwrap();
    let paths = Paths {
        data_home: data_home.clone(),
        state_file: data_home.join("state.toml"),
        lock_file: data_home.join("sync.lock"),
        cache,
        backup: data_home.join("backup/previous"),
        codex_home: codex_home.clone(),
    };
    let backup = create_core_backup(&paths).unwrap();
    fs::write(codex_home.join("config.toml"), "model = \"after\"\n").unwrap();
    fs::write(codex_home.join("AGENTS.md"), "after\n").unwrap();
    restore_core_backup(&paths, &backup).unwrap();
    assert_eq!(
        fs::read_to_string(codex_home.join("config.toml")).unwrap(),
        "model = \"before\"\n"
    );
    assert_eq!(
        fs::read_to_string(codex_home.join("AGENTS.md")).unwrap(),
        "before\n"
    );
}

#[test]
fn restore_rejects_incomplete_named_backup() {
    let temporary = tempfile::tempdir().unwrap();
    let paths = backup_test_paths(temporary.path());
    fs::create_dir_all(paths.backup.join("agents")).unwrap();
    let error = restore_core_backup(&paths, &paths.backup)
        .unwrap_err()
        .to_string();
    assert!(error.contains("backup is incomplete"));
}

#[test]
fn restore_rejects_non_directory_agents_backup() {
    let temporary = tempfile::tempdir().unwrap();
    let paths = backup_test_paths(temporary.path());
    fs::create_dir_all(&paths.backup).unwrap();
    fs::write(paths.backup.join("config.toml"), "model = \"before\"\n").unwrap();
    fs::write(paths.backup.join("AGENTS.md"), "before\n").unwrap();
    fs::write(paths.backup.join("agents"), "not a directory").unwrap();
    assert!(restore_core_backup(&paths, &paths.backup).is_err());
}

#[cfg(unix)]
#[test]
fn restore_rejects_dangling_agents_backup_symlink() {
    use std::os::unix::fs::symlink;
    let temporary = tempfile::tempdir().unwrap();
    let paths = backup_test_paths(temporary.path());
    fs::create_dir_all(&paths.backup).unwrap();
    fs::write(paths.backup.join("config.toml"), "model = \"before\"\n").unwrap();
    fs::write(paths.backup.join("AGENTS.md"), "before\n").unwrap();
    symlink(
        temporary.path().join("missing"),
        paths.backup.join("agents"),
    )
    .unwrap();
    assert!(restore_core_backup(&paths, &paths.backup).is_err());
}
