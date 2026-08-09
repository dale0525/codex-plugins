mod common;

#[cfg(feature = "pdf")]
use common::write_pdf;
use common::{normalized, write};
use fastctx::server::{FastCtxServer, ServerOptions};
use rmcp::ServerHandler;
use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

/// One temporary profile shared by every server this binary spawns.
///
/// An inherited HOME resolves the control-center endpoint, the Codex profile, and the provider
/// guard from the developer's real machine. A third-party provider there activates Guarded mode,
/// which silently rewrites the budget variables these tests set. CI images have no Codex profile,
/// so the failure only ever appears on a developer machine.
fn isolated_home() -> &'static std::path::Path {
    static HOME: OnceLock<tempfile::TempDir> = OnceLock::new();
    HOME.get_or_init(|| tempfile::tempdir().expect("a temporary profile for the test servers"))
        .path()
}

/// Spawns the MCP binary with the control-center idle timeout shared by the test tree.
fn fastctx_command() -> Command {
    fastctx_command_for_home(isolated_home())
}

fn fastctx_command_for_home(home: &std::path::Path) -> Command {
    std::fs::create_dir_all(home).expect("the isolated server profile should be creatable");
    let mut command = Command::new(env!("CARGO_BIN_EXE_fastctx"));
    command
        .env("FASTCTX_TEST_RUNTIME_IDLE_MS", common::TEST_HOST_IDLE_MS)
        .env("HOME", home)
        .env("USERPROFILE", home);
    command
}

#[test]
fn default_tool_definitions_publish_replace_with_explicit_permissions() {
    let tools = FastCtxServer::new().tool_definitions();
    assert_eq!(
        tools
            .iter()
            .map(|tool| tool.name.as_ref())
            .collect::<Vec<_>>(),
        ["glob", "grep", "read", "replace"]
    );
    for tool in &tools {
        let annotations = tool.annotations.as_ref().expect("annotations");
        assert_eq!(
            annotations.read_only_hint,
            Some(tool.name != "replace"),
            "{}",
            tool.name
        );
        assert_eq!(annotations.destructive_hint, Some(false));
        assert_eq!(annotations.open_world_hint, Some(false));
        assert!(tool.output_schema.is_none());
        assert!(tool.input_schema.get("type").is_some());
    }
    let read = tools.iter().find(|tool| tool.name == "read").unwrap();
    assert_eq!(
        read.description.as_deref(),
        Some(concat!(
            "Read one file (text, image, or PDF) or a batch of text files from the local\n",
            "filesystem. Paths must be absolute. Text returns 1-based `N<tab>content`\n",
            "lines, as much of the file as the output budget holds. For several text\n",
            "files in one call, pass files=[{\"path\": ...}, ...] instead of file_path:\n",
            "one token budget, per-file problems reported inline without failing the\n",
            "batch, and a Partial note returns the exact files array for the next call.\n",
            "Images (PNG/JPG/GIF/WebP/BMP) are shown to you visually. PDFs return the\n",
            "selected pages' text layer or those pages rendered as images; image mode\n",
            "defaults to 4 pages. view=\"hex\" dumps any file's raw bytes. PDFs, images,\n",
            "and hex view are single-file only. Text output is always UTF-8; when\n",
            "auto-detection is not confident it returns an error listing candidate\n",
            "encodings instead of guessed text, so pass encoding only then. Text, PDF,\n",
            "and hex responses end with a Complete or Partial status — continue only\n",
            "with the exact parameters a Partial note provides."
        ))
    );
    assert!(
        read.input_schema
            .get("required")
            .is_none_or(|required| required.as_array().is_some_and(Vec::is_empty))
    );
    assert_eq!(read.input_schema["properties"]["files"]["minItems"], 1);
    assert_eq!(read.input_schema["properties"]["files"]["maxItems"], 32);
    assert_eq!(
        read.input_schema["properties"]["files"]["items"]["$ref"],
        "#/$defs/BatchReadEntry"
    );
    assert_eq!(
        read.input_schema["$defs"]["BatchReadEntry"]["required"],
        serde_json::json!(["path"])
    );
    assert_eq!(
        read.input_schema["$defs"]["BatchReadEntry"]["properties"]["offset"]["minimum"],
        1
    );
    assert_eq!(
        read.input_schema["$defs"]["BatchReadEntry"]["properties"]["limit"]["minimum"],
        1
    );
    assert_eq!(read.input_schema["properties"]["offset"]["minimum"], 1);
    assert_eq!(read.input_schema["properties"]["limit"]["minimum"], 1);
    let pdf_mode_schema = read.input_schema["properties"]["pdf_mode"].to_string();
    assert!(pdf_mode_schema.contains("text"));
    assert!(pdf_mode_schema.contains("image"));
    assert!(read.input_schema["properties"].get("encoding").is_some());
    assert_eq!(
        read.input_schema["properties"]["encoding"]["description"],
        "Text files only. Known source encoding as a WHATWG label, e.g. \"gbk\", \"shift_jis\", \"big5\", \"euc-kr\", \"windows-1252\", \"utf-16le\", plus \"utf-32le\"/\"utf-32be\". Selects how source bytes are decoded; output is always UTF-8. Omit for auto-detection; set it when you know the source encoding or the tool reports an ambiguous or undecodable encoding."
    );
    let view_schema = read.input_schema["properties"]["view"].to_string();
    assert!(view_schema.contains("auto"));
    assert!(view_schema.contains("hex"));
    let grep = tools.iter().find(|tool| tool.name == "grep").unwrap();
    assert_eq!(
        grep.description.as_deref(),
        Some(concat!(
            "Fast regex content search (ripgrep engine; Rust regex, no lookaround). Output\n",
            "modes: \"files_with_matches\" (default, paths only), \"content\", \"count\" (total\n",
            "matches, not matching lines), \"summary\" (global totals). Respects .gitignore;\n",
            "searches hidden files; skips .git and binaries. Files are decoded to UTF-8\n",
            "before searching; files whose encoding can't be determined, that change, or\n",
            "that cannot be searched are skipped and listed for directory targets; the\n",
            "equivalent single-file failure returns an error. Matching is line-by-line:\n",
            "`^` and `$` anchor line boundaries and are CRLF-aware. A path component of the\n",
            "form ~fastctx~b...~ (reversible bytes/UTF-8) or ~fastctx~w...~ (Windows UTF-16)\n",
            "is a filename escape; copy that whole component verbatim in later calls and\n",
            "do not decode or rewrite it. The last line of every successful result states\n",
            "Complete or Partial — continue only with the exact offset a Partial note\n",
            "provides; errors are self-contained."
        ))
    );
    assert_eq!(
        grep.input_schema["required"],
        serde_json::json!(["pattern"])
    );
    assert!(grep.input_schema["properties"].get("type").is_some());
    assert!(grep.input_schema["properties"].get("file_type").is_none());
    assert_eq!(
        grep.input_schema["properties"]["encoding"]["description"],
        "Single-file target only: decode that file with this WHATWG encoding label (e.g. \"gbk\"), same semantics as read's encoding. On a directory target use fallback_encoding instead."
    );
    assert_eq!(
        grep.input_schema["properties"]["fallback_encoding"]["description"],
        "Directory target: WHATWG encoding to assume only for files auto-detection can't determine — never overrides BOM, valid UTF-8, or already-resolved files. Strict-decoded; files that also fail under it stay in the skip report."
    );
    let output_mode_schema = grep.input_schema["properties"]["output_mode"].to_string();
    for mode in ["content", "files_with_matches", "count", "summary"] {
        assert!(output_mode_schema.contains(mode), "{output_mode_schema}");
    }
    let glob = tools.iter().find(|tool| tool.name == "glob").unwrap();
    assert_eq!(
        glob.description.as_deref(),
        Some(concat!(
            "Find files by glob pattern, e.g. \"**/*.rs\" or \"src/**/*.ts\". Returns absolute\n",
            "paths sorted by path (or newest first with sort=\"modified\"), 100 per page by\n",
            "default. filter_mode defaults to \"project\" (respects .gitignore, skips .git);\n",
            "\"all\" lists everything. Omit `path` entirely for the session working directory\n",
            "— never pass \"null\" or \"undefined\". A path component of the form ~fastctx~b...~\n",
            "(reversible bytes/UTF-8) or ~fastctx~w...~ (Windows UTF-16) is a filename\n",
            "escape; copy that whole component verbatim in later calls and do not decode or\n",
            "rewrite it. The last line of every successful result states Complete or Partial\n",
            "— continue only with the exact offset a Partial note provides; errors are\n",
            "self-contained."
        ))
    );
    assert_eq!(
        glob.input_schema["required"],
        serde_json::json!(["pattern"])
    );
    for property in ["filter_mode", "sort", "offset", "limit"] {
        assert!(glob.input_schema["properties"].get(property).is_some());
    }
    assert_eq!(glob.input_schema["properties"]["limit"]["minimum"], 1);
    assert_eq!(glob.input_schema["properties"]["limit"]["maximum"], 1_000);
    let descriptions = tools
        .iter()
        .map(|tool| tool.description.as_deref().unwrap_or_default())
        .collect::<Vec<_>>()
        .join(" ");
    for keyword in ["file", "read", "grep", "search", "glob", "replace"] {
        assert!(descriptions.to_ascii_lowercase().contains(keyword));
    }
}

#[test]
fn server_instructions_follow_the_optional_shell_group() {
    for enable_shell in [false, true] {
        let info = FastCtxServer::with_options(ServerOptions { enable_shell }).get_info();
        let instructions = info.instructions.as_deref().unwrap();
        assert_eq!(
            instructions.contains("POSIX-bash"),
            enable_shell,
            "{instructions}"
        );
        assert!(instructions.contains("replace"), "{instructions}");
        // Hosts render these instructions as the tool namespace's one-line blurb and may keep
        // only the first line and first 250 characters, so anything past that budget is
        // silently dropped. Behavioural rules live in the guidance file instead (2026-07-24).
        assert_eq!(instructions.lines().count(), 1, "{instructions}");
        assert!(
            instructions.chars().count() <= 250,
            "instructions must fit the host namespace-description budget, got {}: {instructions}",
            instructions.chars().count()
        );
        // Naming a host resource tool, or resources at all, puts it next to this server's name in
        // every session. 0.2.2 shipped that pairing and users began reporting the very call it
        // forbade, so the blurb introduces the toolset and says nothing else (2026-08-01).
        for banned_tool in [
            "list_mcp_resources",
            "list_mcp_resource_templates",
            "read_mcp_resource",
            "MCP resources",
            "file://",
        ] {
            assert!(!instructions.contains(banned_tool), "{instructions}");
        }
        for removed in ["named clips", "copy", "cut", "paste"] {
            assert!(!instructions.contains(removed), "{instructions}");
        }
    }
}

#[test]
fn all_nine_tools_publish_explicit_three_hint_annotations() {
    let tools = FastCtxServer::with_options(ServerOptions::all()).tool_definitions();
    assert_eq!(tools.len(), 9);

    for tool in &tools {
        let annotations = tool.annotations.as_ref().expect("annotations");
        assert!(annotations.read_only_hint.is_some(), "{}", tool.name);
        assert_eq!(annotations.destructive_hint, Some(false), "{}", tool.name);
        assert_eq!(annotations.open_world_hint, Some(false), "{}", tool.name);
    }
    for name in ["glob", "grep", "job_list", "job_output", "read"] {
        let tool = tools.iter().find(|tool| tool.name == name).unwrap();
        assert_eq!(
            tool.annotations.as_ref().unwrap().read_only_hint,
            Some(true)
        );
    }
    for name in ["run", "run_background", "job_kill", "replace"] {
        let tool = tools.iter().find(|tool| tool.name == name).unwrap();
        assert_eq!(
            tool.annotations.as_ref().unwrap().read_only_hint,
            Some(false)
        );
    }
}

#[test]
fn shell_and_replace_tool_descriptions_and_schemas_match_the_frozen_contract() {
    let tools = FastCtxServer::with_options(ServerOptions::all()).tool_definitions();
    let shell = tools
        .iter()
        .filter(|tool| {
            matches!(
                tool.name.as_ref(),
                "job_kill" | "job_list" | "job_output" | "run" | "run_background"
            )
        })
        .collect::<Vec<_>>();
    assert_eq!(
        shell
            .iter()
            .map(|tool| tool.name.as_ref())
            .collect::<Vec<_>>(),
        [
            "job_kill",
            "job_list",
            "job_output",
            "run",
            "run_background",
        ]
    );
    let run = shell.iter().find(|tool| tool.name == "run").unwrap();
    assert_eq!(
        run.description.as_deref(),
        Some(concat!(
            "Run a shell command with bash (Git Bash on Windows; system bash elsewhere)\n",
            "and return its merged stdout+stderr with the exit code. Write POSIX bash —\n",
            "never PowerShell. Commands must be non-interactive: there is no TTY or\n",
            "stdin; use flags like -y or --no-edit. A non-zero exit code is a normal\n",
            "result, not an error. Oversized output is truncated; for the full output,\n",
            "redirect it to a file (command > file 2>&1) and page that file with read.\n",
            "Default timeout 120000 ms, ceiling 240000 — start anything that may outlast\n",
            "it with run_background. If output looks garbled (U+FFFD), pass encoding\n",
            "(e.g. \"gbk\"). The last line states Complete or Partial."
        ))
    );
    assert_eq!(run.input_schema["required"], serde_json::json!(["command"]));
    assert_eq!(run.input_schema["properties"]["timeout_ms"]["minimum"], 1);
    assert_eq!(
        run.input_schema["properties"]["timeout_ms"]["maximum"],
        240_000
    );
    assert_eq!(
        run.input_schema["properties"]["login_shell"]["default"],
        true
    );
    assert!(run.input_schema["properties"].get("encoding").is_some());
    let background = shell
        .iter()
        .find(|tool| tool.name == "run_background")
        .unwrap();
    assert_eq!(
        background.description.as_deref(),
        Some(concat!(
            "Start a bash command as a background job and return its job_id\n",
            "immediately. Use for builds, tests, servers, or anything that may outlast\n",
            "run's four-minute maximum. Jobs survive server and Codex restarts; their\n",
            "output and exit code stay retrievable by job_id. Check on it with\n",
            "job_output; stop with job_kill; rediscover past jobs with job_list. There\n",
            "is no timeout: a job runs until it exits or is killed. Everything it\n",
            "prints is kept in a plain log file whose path is returned here; read or\n",
            "grep that path for anything job_output does not show. While your jobs\n",
            "run, every FastCtx result carries a one-line background status naming\n",
            "each job and how long it has run, just above the closing Complete or\n",
            "Partial line. It is a readout, not a notification: it refreshes only when\n",
            "you call a tool, so keep working — nothing reaches you if you stop."
        ))
    );
    assert_eq!(
        background.input_schema["required"],
        serde_json::json!(["command"])
    );
    assert!(
        background.input_schema["properties"]
            .get("timeout_ms")
            .is_none()
    );
    assert_eq!(
        background.input_schema["properties"]["login_shell"]["default"],
        true
    );
    assert!(
        background.input_schema["properties"]
            .get("encoding")
            .is_some()
    );
    let output = shell.iter().find(|tool| tool.name == "job_output").unwrap();
    assert_eq!(
        output.description.as_deref(),
        Some(concat!(
            "Query a background job: its status (running, exited with its code, or\n",
            "interrupted) plus output you have not been shown yet. Works for jobs\n",
            "started in earlier sessions. Long output is windowed: the newest lines\n",
            "that fit, the start of the log on the first call, and a note naming the\n",
            "exact lines skipped. The job's whole output is a plain log file on disk\n",
            "whose line numbers are the seq numbers used here, so read or grep that\n",
            "path for anything not shown. The call blocks up to wait_ms, so raise it\n",
            "only when you have nothing else to do. If output looks garbled (U+FFFD),\n",
            "call again with encoding set to the source encoding (e.g. \"gbk\").\n",
            "Complete appears only once the job ends; servers and watchers never reach\n",
            "it. Take what you need and keep working — the background status on your\n",
            "next result carries this job's state."
        ))
    );
    // A running job is always Partial, and a dev server or watch never reaches a terminal
    // state — telling the caller to poll until Complete would prescribe an endless loop
    // (2026-07-24).
    assert!(
        !output
            .description
            .as_deref()
            .unwrap_or_default()
            .contains("until the last line says Complete"),
        "job_output must not prescribe polling to a terminal state"
    );
    assert_eq!(
        output.input_schema["required"],
        serde_json::json!(["job_id"])
    );
    assert_eq!(output.input_schema["properties"]["wait_ms"]["minimum"], 0);
    assert_eq!(
        output.input_schema["properties"]["wait_ms"]["maximum"],
        240_000
    );
    assert_eq!(
        output.input_schema["properties"]["wait_ms"]["default"],
        30_000
    );
    assert!(output.input_schema["properties"].get("wait_for").is_none());
    assert_eq!(output.input_schema["properties"]["after_seq"]["minimum"], 0);
    assert!(output.input_schema["properties"].get("encoding").is_some());
    let list = shell.iter().find(|tool| tool.name == "job_list").unwrap();
    assert_eq!(
        list.description.as_deref(),
        Some(concat!(
            "List background jobs across all FastCtx sessions for the current user. Use\n",
            "status=\"all\" only when both lifecycles are needed. Results are newest first\n",
            "within each lifecycle. Finished records remain available until the job\n",
            "storage limit evicts the oldest."
        ))
    );
    assert!(
        list.input_schema
            .get("required")
            .is_none_or(|required| required.as_array().is_some_and(Vec::is_empty))
    );
    assert_eq!(
        list.input_schema["properties"]["status"]["$ref"],
        "#/$defs/JobListStatus"
    );
    assert_eq!(
        list.input_schema["$defs"]["JobListStatus"]["oneOf"]
            .as_array()
            .unwrap()
            .iter()
            .map(|option| option["const"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["running", "finished", "all"]
    );
    assert_eq!(list.input_schema["properties"]["limit"]["minimum"], 1);
    assert_eq!(list.input_schema["properties"]["limit"]["maximum"], 100);
    assert_eq!(list.input_schema["properties"]["offset"]["minimum"], 0);

    let replace = tools.iter().find(|tool| tool.name == "replace").unwrap();
    assert_eq!(
        replace.description.as_deref(),
        Some(concat!(
            "Batch find-and-replace across a file or directory (Rust regex, same engine\n",
            "as grep; no lookaround). A reference to an undefined capture group is\n",
            "rejected before any write. To delete whole lines, include \\n in the\n",
            "pattern. Matching is leftmost-first and non-overlapping; unlike grep,\n",
            "`^`/`$` anchor the whole file by default — use (?m) for per-line anchors.\n",
            "Respects .gitignore; skips .git and binaries; files whose encoding cannot\n",
            "be determined are skipped and listed. Each file is written atomically with\n",
            "a concurrent-modification check, preserving its original encoding, BOM, and\n",
            "line endings. The last line states Complete or Partial."
        ))
    );

    let descriptions = tools
        .iter()
        .filter_map(|tool| tool.description.as_deref())
        .collect::<Vec<_>>()
        .join("\n");
    let job_notes = format!(
        "{}\n{}",
        include_str!("../src/shell/output.rs"),
        include_str!("../src/shell/jobs/mod.rs")
    );
    for forbidden in [
        "will be told",
        "will be notified",
        "wait for it to tell you",
        "notify you when",
        "tell you when it",
        "wait until notified",
    ] {
        assert!(
            !descriptions.to_ascii_lowercase().contains(forbidden),
            "tool descriptions must not promise a push notification: {forbidden}"
        );
        assert!(
            !job_notes.to_ascii_lowercase().contains(forbidden),
            "running and terminal notes must not promise push delivery: {forbidden}"
        );
    }
    assert!(
        !job_notes.to_ascii_lowercase().contains("notification"),
        "only the run_background description may use notification, and only in its explicit negation"
    );
    assert!(descriptions.contains("It is a readout, not a notification"));
    assert_eq!(
        replace.input_schema["required"],
        serde_json::json!(["pattern", "replacement", "path"])
    );
    for property in [
        "glob",
        "literal",
        "case_insensitive",
        "dot_all",
        "max_replacements",
        "dry_run",
        "encoding",
        "fallback_encoding",
    ] {
        assert!(
            replace.input_schema["properties"].get(property).is_some(),
            "{property}"
        );
    }
}

#[test]
fn stdio_glob_uses_the_server_working_directory_when_path_is_omitted() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("cwd.txt");
    write(&file, b"cwd");
    let mut child = fastctx_command()
        .current_dir(temp.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());

    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "cwd-test", "version": "1.0"}
            }
        }),
    );
    let _ = read_response(&mut stdout);
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
    );
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc":"2.0",
            "id":2,
            "method":"tools/call",
            "params":{"name":"glob","arguments":{"pattern":"*.txt"}}
        }),
    );
    let response = read_response(&mut stdout);
    assert_eq!(response["result"]["isError"], false);
    assert!(response["result"].get("structuredContent").is_none());
    assert_eq!(
        response["result"]["content"][0]["text"],
        format!("{}\n\n(Complete: all 1 file shown.)", normalized(&file))
    );

    drop(stdin);
    assert!(child.wait().unwrap().success());
}

#[test]
fn non_pdf_stdio_calls_do_not_extract_the_bundled_engine() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("plain.txt");
    write(&file, b"plain");
    let cache_root = temp.path().join("cache-root");
    let home = temp.path().join("home");
    let mut command = fastctx_command_for_home(&home);
    command
        .current_dir(temp.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let expected_engine_dir = configure_isolated_cache(&mut command, &cache_root);
    let mut child = command.spawn().unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "lazy-test", "version": "1.0"}
            }
        }),
    );
    let _ = read_response(&mut stdout);
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
    );
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc":"2.0",
            "id":2,
            "method":"tools/call",
            "params":{"name":"read","arguments":{"file_path":normalized(&file)}}
        }),
    );
    let response = read_response(&mut stdout);
    assert_eq!(response["result"]["isError"], false);
    assert_eq!(
        response["result"]["content"][0]["text"],
        "1\tplain\n\n(Complete: reached end of file; line 1 of 1 shown.)"
    );
    drop(stdin);
    assert!(child.wait().unwrap().success());
    if expected_engine_dir.exists() {
        let direct_files = std::fs::read_dir(&expected_engine_dir)
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_file()))
            .map(|entry| entry.file_name())
            .collect::<Vec<_>>();
        assert!(
            direct_files.is_empty(),
            "a non-PDF call extracted cache files: {direct_files:?}"
        );
    }
}

#[test]
#[cfg(feature = "pdf")]
fn stdio_pdf_call_extracts_one_hashed_engine_and_preserves_image_meta() {
    let temp = tempfile::tempdir().unwrap();
    let pdf = temp.path().join("page.pdf");
    write_pdf(&pdf, &[Some("MCP PDF one"), Some("MCP PDF two")]);
    let cache_root = temp.path().join("cache-root");
    let home = temp.path().join("home");
    let mut command = fastctx_command_for_home(&home);
    command
        .current_dir(temp.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let engine_dir = configure_isolated_cache(&mut command, &cache_root);
    let mut child = command.spawn().unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pdf-test", "version": "1.0"}
            }
        }),
    );
    let _ = read_response(&mut stdout);
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
    );
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc":"2.0",
            "id":2,
            "method":"tools/call",
            "params":{"name":"read","arguments":{"file_path":normalized(&pdf),"pdf_mode":"image"}}
        }),
    );
    let response = read_response(&mut stdout);
    assert_eq!(response["result"]["isError"], false);
    assert!(response["result"].get("structuredContent").is_none());
    assert_eq!(response["result"]["content"].as_array().unwrap().len(), 3);
    assert_eq!(response["result"]["content"][0]["type"], "image");
    assert_eq!(response["result"]["content"][1]["type"], "image");
    assert_eq!(response["result"]["content"][2]["type"], "text");
    assert_eq!(
        response["result"]["content"][2]["text"],
        "(Complete: pages 1-2 of 2 rendered.)"
    );
    for image_index in [0, 1] {
        assert_eq!(
            response["result"]["content"][image_index]["_meta"]["codex/imageDetail"],
            "high"
        );
    }
    drop(stdin);
    assert!(child.wait().unwrap().success());

    let released = std::fs::read_dir(&engine_dir)
        .unwrap()
        .map(|entry| entry.unwrap())
        .filter(|entry| {
            entry.file_type().unwrap().is_file()
                && !entry.file_name().to_string_lossy().ends_with(".lock")
        })
        .collect::<Vec<_>>();
    assert_eq!(released.len(), 1, "{released:?}");
    let name = released[0].file_name().to_string_lossy().into_owned();
    assert!(name.contains("chromium-7763"));
    assert!(released[0].metadata().unwrap().len() > 1_000_000);
}

#[test]
fn stdio_mcp_is_tool_only_lists_tools_and_never_returns_structured_content() {
    let temp = tempfile::tempdir().unwrap();
    let mut child = fastctx_command()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let stdout = child.stdout.take().unwrap();
    let mut stdout = BufReader::new(stdout);

    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "contract-test", "version": "1.0"}
            }
        }),
    );
    let initialized = read_response(&mut stdout);
    assert_eq!(initialized["id"], 1);
    assert!(initialized["result"]["capabilities"]["tools"].is_object());
    assert!(
        initialized["result"]["capabilities"]
            .get("resources")
            .is_none(),
        "{initialized}"
    );
    let instructions = initialized["result"]["instructions"].as_str().unwrap();
    assert!(instructions.contains("Local-file tools"), "{instructions}");
    assert!(!instructions.contains("MCP resources"), "{instructions}");
    assert!(instructions.chars().count() <= 250, "{instructions}");
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
    );
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}),
    );
    let listed = read_response(&mut stdout);
    assert_eq!(listed["result"]["tools"].as_array().unwrap().len(), 4);

    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc":"2.0",
            "id":3,
            "method":"tools/call",
            "params":{"name":"read","arguments":{"file_path":"Z:/definitely/missing.txt"}}
        }),
    );
    let called = read_response(&mut stdout);
    assert_eq!(called["result"]["isError"], true);
    assert!(called["result"].get("structuredContent").is_none());
    assert_eq!(called["result"]["content"][0]["type"], "text");

    // Both discovery methods keep the rmcp default: an empty list, which answers "this server
    // has none" without failing. Rejecting them instead (0.2.2) made every misrouted call a
    // failure, and a failed call makes models retry with a different `server` argument rather
    // than switch tools, which is the chain of invented server names users hit (2026-08-01).
    for (id, method, key) in [
        (4, "resources/list", "resources"),
        (5, "resources/templates/list", "resourceTemplates"),
    ] {
        send(
            &mut stdin,
            serde_json::json!({"jsonrpc":"2.0","id":id,"method":method,"params":{}}),
        );
        let listed = read_response(&mut stdout);
        assert_eq!(listed["id"], id, "{method}");
        assert!(listed.get("error").is_none(), "{method}: {listed}");
        assert_eq!(
            listed["result"][key].as_array().map(Vec::len),
            Some(0),
            "{method}: {listed}"
        );
    }

    // `resources/read` stays method-not-found for every URI shape, including one that names a
    // real readable file. Serving it would build a second file-reading contract outside the
    // annotated tool surface, and one whose own `Partial` note would name continuation
    // parameters `resources/read` has no field to carry.
    let sentinel_body = "d0f4b2e7-sentinel-never-served";
    let sentinel = temp.path().join("sentinel.txt");
    write(&sentinel, format!("{sentinel_body}\n"));
    for (id, uri) in [
        (6, format!("file:///{}", normalized(&sentinel))),
        (7, sentinel.to_string_lossy().into_owned()),
        (8, "file:///Z:/definitely/missing.txt".to_string()),
    ] {
        send(
            &mut stdin,
            serde_json::json!({
                "jsonrpc":"2.0","id":id,"method":"resources/read","params":{"uri":&uri}
            }),
        );
        let rejected = read_response(&mut stdout);
        assert_eq!(rejected["id"], id, "{uri}");
        assert_eq!(rejected["error"]["code"], -32601, "{uri}: {rejected}");
        assert!(rejected.get("result").is_none(), "{uri}: {rejected}");
        assert!(
            !rejected.to_string().contains(sentinel_body),
            "{uri}: {rejected}"
        );
    }

    drop(stdin);
    let status = child.wait().unwrap();
    assert!(status.success());
}

#[test]
fn stdio_serve_flags_publish_exact_four_and_nine_tool_sets() {
    let cases: [(&[&str], &[&str]); 4] = [
        (&["serve"], &["glob", "grep", "read", "replace"]),
        (
            &["serve", "--enable-shell"],
            &[
                "glob",
                "grep",
                "job_kill",
                "job_list",
                "job_output",
                "read",
                "replace",
                "run",
                "run_background",
            ],
        ),
        (
            &["serve", "--enable-edit"],
            &["glob", "grep", "read", "replace"],
        ),
        (
            &["serve", "--enable-shell", "--enable-edit"],
            &[
                "glob",
                "grep",
                "job_kill",
                "job_list",
                "job_output",
                "read",
                "replace",
                "run",
                "run_background",
            ],
        ),
    ];

    for (args, expected) in cases {
        assert_eq!(list_tool_names(args), expected, "args={args:?}");
    }
}

#[test]
fn stdio_head_limit_zero_still_uses_the_environment_token_budget() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("many.txt");
    write(&file, "hit\n".repeat(100));
    let mut command = fastctx_command();
    command
        .env("HOME", temp.path())
        .env("USERPROFILE", temp.path())
        .env_remove("CODEX_HOME")
        .env_remove("FASTCTX_GREP_TOKEN_BUDGET")
        .env("FASTCTX_TOKEN_BUDGET", "30");
    let response = call_tool(
        command,
        "grep",
        serde_json::json!({
            "pattern": "hit",
            "path": normalized(&file),
            "output_mode": "content",
            "head_limit": 0
        }),
    );
    assert_eq!(response["result"]["isError"], false);
    assert_eq!(
        response["result"]["content"][0]["text"],
        "1:hit\n2:hit\n\n(Partial: results 1-2 shown; more exist. Continue with offset=2.)"
    );
}

#[test]
fn stdio_preserves_utf8_text_without_host_codepage_transcoding() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("unicode.txt");
    write(&file, "alpha\n中文 sentinel\n".as_bytes());
    let response = call_tool(
        fastctx_command(),
        "read",
        serde_json::json!({"file_path": normalized(&file)}),
    );
    assert_eq!(response["result"]["isError"], false);
    assert_eq!(
        response["result"]["content"][0]["text"],
        "1\talpha\n2\t中文 sentinel\n3\t\n\n(Complete: reached end of file; lines 1-3 of 3 shown.)"
    );
}

#[test]
fn stdio_invalid_token_budget_is_an_exact_tool_error() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("plain.txt");
    write(&file, b"plain");
    let mut command = fastctx_command();
    command.env("FASTCTX_TOKEN_BUDGET", "0");
    let response = call_tool(
        command,
        "read",
        serde_json::json!({"file_path": normalized(&file)}),
    );
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(
        response["result"]["content"][0]["text"],
        "Invalid FASTCTX_TOKEN_BUDGET value \"0\": expected a positive integer."
    );
}

#[test]
fn stdio_batch_read_requires_room_for_one_line_and_its_exact_continuation() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("plain.txt");
    write(&file, b"plain\nmore");
    let mut command = fastctx_command();
    command
        .env("FASTCTX_TOKEN_BUDGET", "10")
        .env("FASTCTX_READ_TOKEN_BUDGET", "1");
    let response = call_tool(
        command,
        "read",
        serde_json::json!({"files": [{"path": normalized(&file)}]}),
    );
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(
        response["result"]["content"][0]["text"],
        "FASTCTX_READ_TOKEN_BUDGET=1 is too small to return the required continuation note. Increase it and retry."
    );
}

#[test]
fn stdio_per_tool_budgets_must_not_exceed_the_global_budget() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("plain.txt");
    write(&file, b"plain");
    let cases = [
        (
            "read",
            "FASTCTX_READ_TOKEN_BUDGET",
            serde_json::json!({"file_path": normalized(&file)}),
        ),
        (
            "grep",
            "FASTCTX_GREP_TOKEN_BUDGET",
            serde_json::json!({"pattern": "plain", "path": normalized(&file)}),
        ),
        (
            "glob",
            "FASTCTX_GLOB_TOKEN_BUDGET",
            serde_json::json!({"pattern": "*.txt", "path": normalized(temp.path())}),
        ),
    ];

    for (tool, variable, arguments) in cases {
        let mut command = fastctx_command();
        command
            .env("FASTCTX_TOKEN_BUDGET", "100")
            .env(variable, "101");
        let response = call_tool(command, tool, arguments);
        assert_eq!(response["result"]["isError"], true);
        assert_eq!(
            response["result"]["content"][0]["text"],
            format!(
                "{variable}=101 exceeds FASTCTX_TOKEN_BUDGET=100. Increase the global budget or lower the per-tool budget."
            )
        );
    }
}

#[test]
fn stdio_per_tool_budgets_reject_non_positive_values() {
    let temp = tempfile::tempdir().unwrap();
    let file = temp.path().join("plain.txt");
    write(&file, b"plain");
    let cases = [
        (
            "read",
            "FASTCTX_READ_TOKEN_BUDGET",
            serde_json::json!({"file_path": normalized(&file)}),
        ),
        (
            "grep",
            "FASTCTX_GREP_TOKEN_BUDGET",
            serde_json::json!({"pattern": "plain", "path": normalized(&file)}),
        ),
        (
            "glob",
            "FASTCTX_GLOB_TOKEN_BUDGET",
            serde_json::json!({"pattern": "*.txt", "path": normalized(temp.path())}),
        ),
    ];

    for (tool, variable, arguments) in cases {
        let mut command = fastctx_command();
        command.env(variable, "0");
        let response = call_tool(command, tool, arguments);
        assert_eq!(response["result"]["isError"], true);
        assert_eq!(
            response["result"]["content"][0]["text"],
            format!("Invalid {variable} value \"0\": expected a positive integer.")
        );
    }
}

#[test]
#[cfg(feature = "pdf")]
fn stdio_pdf_text_mode_uses_the_read_specific_page_budget() {
    let temp = tempfile::tempdir().unwrap();
    let pdf = temp.path().join("budget.pdf");
    let long_page = "x".repeat(5_000);
    write_pdf(&pdf, &[Some("Small"), Some(long_page.as_str())]);
    let mut command = fastctx_command();
    command.env("FASTCTX_READ_TOKEN_BUDGET", "34");
    let response = call_tool(
        command,
        "read",
        serde_json::json!({"file_path": normalized(&pdf), "pages": "1-2"}),
    );
    assert_eq!(response["result"]["isError"], false);
    assert_eq!(
        response["result"]["content"][0]["text"],
        "=== Page 1 ===\nSmall\n\n(Partial: page 1 of 2 shown. Continue with pages=\"2\".)"
    );
}

#[test]
#[cfg(feature = "pdf")]
// Repair only happens on a control center that has not already released the engine, so each half
// of this test needs a private one. `FASTCTX_TEST_BUILD_ID` is the only way to get that, and it is
// debug-only: in a release build both halves share one host and the second never re-extracts.
#[cfg(debug_assertions)]
fn stdio_pdf_call_repairs_a_corrupted_cached_engine() {
    let temp = tempfile::tempdir().unwrap();
    let pdf = temp.path().join("page.pdf");
    write_pdf(&pdf, &[Some("Cache repair")]);
    let cache_root = temp.path().join("cache-root");
    let process_id = std::process::id();

    let mut first_command = fastctx_command();
    first_command.env(
        "FASTCTX_TEST_BUILD_ID",
        format!("pdf-repair-a-{process_id}"),
    );
    let engine_dir = configure_isolated_cache(&mut first_command, &cache_root);
    let first = call_tool(
        first_command,
        "read",
        serde_json::json!({"file_path": normalized(&pdf)}),
    );
    assert_eq!(first["result"]["isError"], false);
    let engine = std::fs::read_dir(&engine_dir)
        .unwrap()
        .map(|entry| entry.unwrap())
        .find(|entry| {
            entry.file_type().unwrap().is_file()
                && !entry.file_name().to_string_lossy().ends_with(".lock")
        })
        .unwrap()
        .path();
    let original = std::fs::read(&engine).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&engine, std::fs::Permissions::from_mode(0o600)).unwrap();
    }
    std::fs::write(&engine, b"corrupted").unwrap();

    let mut second_command = fastctx_command();
    second_command.env(
        "FASTCTX_TEST_BUILD_ID",
        format!("pdf-repair-b-{process_id}"),
    );
    configure_isolated_cache(&mut second_command, &cache_root);
    let second = call_tool(
        second_command,
        "read",
        serde_json::json!({"file_path": normalized(&pdf)}),
    );
    assert_eq!(second["result"]["isError"], false);
    assert_eq!(std::fs::read(engine).unwrap(), original);
}

#[test]
#[cfg(all(feature = "pdf", any(windows, all(unix, not(target_os = "macos")))))]
fn stdio_pdf_initialization_uses_the_request_session_cache_environment() {
    let temp = tempfile::tempdir().unwrap();
    let text = temp.path().join("plain.txt");
    let pdf = temp.path().join("page.pdf");
    write(&text, b"plain\n");
    write_pdf(&pdf, &[Some("Session cache")]);
    let bootstrap_cache = temp.path().join("bootstrap-cache");
    let request_cache = temp.path().join("request-cache");
    let home = temp.path().join("home");

    // A shared private HOME selects one fresh control center in every build profile; unlike the
    // debug-only build-id hook, this also keeps release tests isolated from an earlier PDF user.
    let mut bootstrap = fastctx_command_for_home(&home);
    let bootstrap_engine = configure_isolated_cache(&mut bootstrap, &bootstrap_cache);
    let first = call_tool(
        bootstrap,
        "read",
        serde_json::json!({"file_path": normalized(&text)}),
    );
    assert_eq!(first["result"]["isError"], false);

    let mut request = fastctx_command_for_home(&home);
    let request_engine = configure_isolated_cache(&mut request, &request_cache);
    let second = call_tool(
        request,
        "read",
        serde_json::json!({"file_path": normalized(&pdf)}),
    );
    assert_eq!(second["result"]["isError"], false);
    assert!(
        std::fs::read_dir(&request_engine)
            .unwrap()
            .filter_map(Result::ok)
            .any(|entry| {
                entry.file_type().is_ok_and(|kind| kind.is_file())
                    && !entry.file_name().to_string_lossy().ends_with(".lock")
            }),
        "PDF initialization did not release the engine into the request cache"
    );
    assert!(
        !bootstrap_engine.exists()
            || std::fs::read_dir(&bootstrap_engine)
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_file()))
                .all(|entry| entry.file_name().to_string_lossy().ends_with(".lock")),
        "PDF initialization used the control center's bootstrap cache"
    );
}

#[test]
#[cfg(not(feature = "pdf"))]
fn no_pdf_build_rejects_pdf_without_affecting_the_public_read_schema() {
    let temp = tempfile::tempdir().unwrap();
    let pdf = temp.path().join("disabled.pdf");
    write(&pdf, b"%PDF-1.4\n");
    let response = call_tool(
        fastctx_command(),
        "read",
        serde_json::json!({"file_path": normalized(&pdf)}),
    );
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(
        response["result"]["content"][0]["text"],
        "PDF support is unavailable: could not load the bundled PDF engine (this binary was built without the pdf feature). Other file types are unaffected."
    );
}

fn send(stdin: &mut impl Write, value: Value) {
    writeln!(stdin, "{}", serde_json::to_string(&value).unwrap()).unwrap();
    stdin.flush().unwrap();
}

fn read_response(reader: &mut impl BufRead) -> Value {
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    serde_json::from_str(&line).unwrap()
}

fn list_tool_names(args: &[&str]) -> Vec<String> {
    let mut child = fastctx_command()
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "tool-list-contract", "version": "1.0"}
            }
        }),
    );
    let initialized = read_response(&mut stdout);
    assert_eq!(initialized["id"], 1);
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
    );
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}),
    );
    let listed = read_response(&mut stdout);
    let mut names = listed["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap().to_string())
        .collect::<Vec<_>>();
    names.sort();
    drop(stdin);
    assert!(child.wait().unwrap().success());
    names
}

fn call_tool(mut command: Command, name: &str, arguments: Value) -> Value {
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "helper", "version": "1.0"}
            }
        }),
    );
    let _ = read_response(&mut stdout);
    send(
        &mut stdin,
        serde_json::json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
    );
    send(
        &mut stdin,
        serde_json::json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        }),
    );
    let response = read_response(&mut stdout);
    drop(stdin);
    assert!(child.wait().unwrap().success());
    response
}

fn configure_isolated_cache(command: &mut Command, root: &std::path::Path) -> std::path::PathBuf {
    #[cfg(windows)]
    {
        command.env("LOCALAPPDATA", root);
        root.join("fastctx")
    }
    #[cfg(target_os = "macos")]
    {
        command.env("HOME", root);
        root.join("Library/Caches/fastctx")
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        command.env("XDG_CACHE_HOME", root);
        root.join("fastctx")
    }
}
