//! Unified rmcp registration, feature gating, and shared tool state.

use crate::budget::{GLOB_TOKEN_BUDGET_ENV, GREP_TOKEN_BUDGET_ENV, READ_TOKEN_BUDGET_ENV};
use crate::edit::ReplaceService;
use crate::file_executor::GrepGlobExecutor;
use crate::glob_tool::{GlobRequest, glob_files_cancellable};
use crate::grep_tool::{GrepRequest, grep_files_cancellable};
use crate::read_tool::{ReadRequest, read_file};
use crate::server_manifest::{ToolContract, ToolManifest};
use crate::server_support::{
    BudgetRetry, CancellableBlockingRequest, run_blocking, run_blocking_cancellable,
};
use crate::session::SessionContext;
use crate::shell::FastShell;
use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::handler::server::wrapper::Parameters;
use rmcp::model::{CallToolResult, Implementation, ServerCapabilities, ServerInfo};
use rmcp::service::RequestContext;
use rmcp::{RoleServer, ServerHandler, tool, tool_handler, tool_router};
use std::sync::Arc;
use tokio::sync::Semaphore;

const MAX_FILE_OPERATIONS: usize = 8;
const MAX_SHELL_OPERATIONS: usize = 16;
const MAX_REPLACE_OPERATIONS: usize = 8;

/// Optional tool groups published by the single `fastctx` server.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct ServerOptions {
    /// Publish the five shell tools.
    pub enable_shell: bool,
}

impl ServerOptions {
    /// Enables all nine tools; intended for contract tests and doctor probes.
    pub const fn all() -> Self {
        Self { enable_shell: true }
    }
}

/// The single stateful MCP server for default file tools and the optional shell group.
#[derive(Clone, Debug)]
pub struct FastCtxServer {
    tool_router: ToolRouter<Self>,
    options: ServerOptions,
    pub(crate) shell: FastShell,
    pub(crate) replace: ReplaceService,
    pub(crate) file_permits: Arc<Semaphore>,
    pub(crate) grep_glob_executor: Arc<GrepGlobExecutor>,
    pub(crate) shell_permits: Arc<Semaphore>,
    pub(crate) replace_permits: Arc<Semaphore>,
    pub(crate) session: Arc<SessionContext>,
    pub(crate) activity: Arc<crate::runtime::activity::RuntimeActivity>,
}

/// Expensive executors and process-wide admission gates shared by every control-center session.
#[derive(Clone, Debug)]
pub struct SharedRuntime {
    file_permits: Arc<Semaphore>,
    grep_glob_executor: Arc<GrepGlobExecutor>,
    shell_permits: Arc<Semaphore>,
    replace: ReplaceService,
    replace_permits: Arc<Semaphore>,
    activity: Arc<crate::runtime::activity::RuntimeActivity>,
}

impl SharedRuntime {
    /// Creates one per-user runtime around the configured search executor.
    pub(crate) fn new(grep_glob_executor: Arc<GrepGlobExecutor>) -> Arc<Self> {
        Self::with_activity(
            grep_glob_executor,
            crate::runtime::activity::RuntimeActivity::new(),
        )
    }

    pub(crate) fn with_activity(
        grep_glob_executor: Arc<GrepGlobExecutor>,
        activity: Arc<crate::runtime::activity::RuntimeActivity>,
    ) -> Arc<Self> {
        Arc::new(Self {
            file_permits: Arc::new(Semaphore::new(MAX_FILE_OPERATIONS)),
            grep_glob_executor,
            shell_permits: Arc::new(Semaphore::new(MAX_SHELL_OPERATIONS)),
            replace: ReplaceService::new(),
            replace_permits: Arc::new(Semaphore::new(MAX_REPLACE_OPERATIONS)),
            activity,
        })
    }
}

impl FastCtxServer {
    /// Creates the default four-tool server, including byte-preserving replacement.
    pub fn new() -> Self {
        Self::with_options(ServerOptions::default())
    }

    /// Creates one server whose visible tools are selected by startup flags.
    pub fn with_options(options: ServerOptions) -> Self {
        Self::with_options_and_executor(options, GrepGlobExecutor::shared())
    }

    /// Creates a server with the process-startup search executor selected by current-user config.
    pub(crate) fn with_options_and_executor(
        options: ServerOptions,
        grep_glob_executor: Arc<GrepGlobExecutor>,
    ) -> Self {
        Self::with_session_and_runtime(
            options,
            SessionContext::library_default(),
            SharedRuntime::new(grep_glob_executor),
        )
    }

    /// Creates one isolated MCP connection backed by a shared per-user runtime.
    pub(crate) fn with_session_and_runtime(
        options: ServerOptions,
        session: Arc<SessionContext>,
        runtime: Arc<SharedRuntime>,
    ) -> Self {
        let mut tool_router = Self::file_tool_router();
        tool_router.merge(Self::shell_tool_router());
        tool_router.merge(Self::edit_tool_router());
        for entry in ToolManifest::entries() {
            if !entry.group.enabled(options.enable_shell) {
                tool_router.remove_route(entry.name);
            }
        }
        let definitions = tool_router.list_all();
        ToolManifest::validate(&definitions, options.enable_shell)
            .expect("the compiled tool router must match ToolManifest");
        Self {
            tool_router,
            options,
            shell: FastShell::with_session(Arc::clone(&session)),
            replace: runtime.replace.clone(),
            file_permits: Arc::clone(&runtime.file_permits),
            grep_glob_executor: Arc::clone(&runtime.grep_glob_executor),
            shell_permits: Arc::clone(&runtime.shell_permits),
            replace_permits: Arc::clone(&runtime.replace_permits),
            activity: Arc::clone(&runtime.activity),
            session,
        }
    }

    /// Returns every definition exposed by MCP `tools/list` for tests and diagnostics.
    pub fn tool_definitions(&self) -> Vec<rmcp::model::Tool> {
        self.tool_router.list_all()
    }

    /// Returns stable contract hashes for every currently published tool.
    pub fn tool_contracts(&self) -> Vec<ToolContract> {
        ToolManifest::contracts(&self.tool_definitions())
            .expect("validated server tools must have manifest entries")
    }

    /// Returns the startup feature selection used by this server.
    pub const fn options(&self) -> ServerOptions {
        self.options
    }
}

impl Default for FastCtxServer {
    fn default() -> Self {
        Self::new()
    }
}

#[tool_router(router = file_tool_router, vis = "pub(crate)")]
impl FastCtxServer {
    #[tool(
        name = "read",
        description = "Read one file (text, image, or PDF) or a batch of text files from the local
filesystem. Paths must be absolute. Text returns 1-based `N<tab>content`
lines, as much of the file as the output budget holds. For several text
files in one call, pass files=[{\"path\": ...}, ...] instead of file_path:
one token budget, per-file problems reported inline without failing the
batch, and a Partial note returns the exact files array for the next call.
Images (PNG/JPG/GIF/WebP/BMP) are shown to you visually. PDFs return the
selected pages' text layer or those pages rendered as images; image mode
defaults to 4 pages. view=\"hex\" dumps any file's raw bytes. PDFs, images,
and hex view are single-file only. Text output is always UTF-8; when
auto-detection is not confident it returns an error listing candidate
encodings instead of guessed text, so pass encoding only then. Text, PDF,
and hex responses end with a Complete or Partial status — continue only
with the exact parameters a Partial note provides.",
        annotations(
            title = "Read local file",
            read_only_hint = true,
            destructive_hint = false,
            open_world_hint = false
        )
    )]
    async fn read(&self, Parameters(request): Parameters<ReadRequest>) -> CallToolResult {
        let _activity = self.activity.request();
        let status_shell = self.shell.clone();
        run_blocking(
            Arc::clone(&self.session),
            Arc::clone(&self.file_permits),
            READ_TOKEN_BUDGET_ENV,
            move || status_shell.background_status(None),
            BudgetRetry::Safe,
            move || read_file(request.clone()),
        )
        .await
    }

    #[tool(
        name = "grep",
        description = "Fast regex content search (ripgrep engine; Rust regex, no lookaround). Output\nmodes: \"files_with_matches\" (default, paths only), \"content\", \"count\" (total\nmatches, not matching lines), \"summary\" (global totals). Respects .gitignore;\nsearches hidden files; skips .git and binaries. Files are decoded to UTF-8\nbefore searching; files whose encoding can't be determined, that change, or\nthat cannot be searched are skipped and listed for directory targets; the\nequivalent single-file failure returns an error. Matching is line-by-line:\n`^` and `$` anchor line boundaries and are CRLF-aware. A path component of the\nform ~fastctx~b...~ (reversible bytes/UTF-8) or ~fastctx~w...~ (Windows UTF-16)\nis a filename escape; copy that whole component verbatim in later calls and\ndo not decode or rewrite it. The last line of every successful result states\nComplete or Partial — continue only with the exact offset a Partial note\nprovides; errors are self-contained.",
        annotations(
            title = "Search file contents",
            read_only_hint = true,
            destructive_hint = false,
            open_world_hint = false
        )
    )]
    async fn grep(
        &self,
        Parameters(request): Parameters<GrepRequest>,
        context: RequestContext<RoleServer>,
    ) -> CallToolResult {
        let _activity = self.activity.request();
        run_blocking_cancellable(
            CancellableBlockingRequest::new(
                Arc::clone(&self.session),
                context.id,
                context.ct,
                Arc::clone(&self.file_permits),
                Arc::clone(&self.grep_glob_executor),
                GREP_TOKEN_BUDGET_ENV,
            ),
            {
                let shell = self.shell.clone();
                move || shell.background_status(None)
            },
            move |operation, executor| grep_files_cancellable(operation, executor, request.clone()),
        )
        .await
    }

    #[tool(
        name = "glob",
        description = "Find files by glob pattern, e.g. \"**/*.rs\" or \"src/**/*.ts\". Returns absolute\npaths sorted by path (or newest first with sort=\"modified\"), 100 per page by\ndefault. filter_mode defaults to \"project\" (respects .gitignore, skips .git);\n\"all\" lists everything. Omit `path` entirely for the session working directory\n— never pass \"null\" or \"undefined\". A path component of the form ~fastctx~b...~\n(reversible bytes/UTF-8) or ~fastctx~w...~ (Windows UTF-16) is a filename\nescape; copy that whole component verbatim in later calls and do not decode or\nrewrite it. The last line of every successful result states Complete or Partial\n— continue only with the exact offset a Partial note provides; errors are\nself-contained.",
        annotations(
            title = "Match file paths",
            read_only_hint = true,
            destructive_hint = false,
            open_world_hint = false
        )
    )]
    async fn glob(
        &self,
        Parameters(request): Parameters<GlobRequest>,
        context: RequestContext<RoleServer>,
    ) -> CallToolResult {
        let _activity = self.activity.request();
        run_blocking_cancellable(
            CancellableBlockingRequest::new(
                Arc::clone(&self.session),
                context.id,
                context.ct,
                Arc::clone(&self.file_permits),
                Arc::clone(&self.grep_glob_executor),
                GLOB_TOKEN_BUDGET_ENV,
            ),
            {
                let shell = self.shell.clone();
                move || shell.background_status(None)
            },
            move |operation, executor| glob_files_cancellable(operation, executor, request.clone()),
        )
        .await
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for FastCtxServer {
    fn get_info(&self) -> ServerInfo {
        self.activity.touch();
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new(
                env!("CARGO_PKG_NAME"),
                env!("CARGO_PKG_VERSION"),
            ))
            // 2026-07-24: hosts render these instructions as the tool namespace's one-line
            // blurb and may keep only its first line and first 250 characters, so this text
            // has to introduce the toolset within that budget. Behavioural rules belong in
            // the host guidance file, which has no such limit.
            .with_instructions(if self.options.enable_shell {
                "Local-file tools: read (one file or a batch), grep (content search), glob (find paths), replace (mechanical find-and-replace), plus POSIX-bash shell tools. Pass absolute paths."
            } else {
                "Local-file tools: read (one file or a batch), grep (content search), glob (find paths), and replace (mechanical find-and-replace). Pass absolute paths."
            })
    }

    // The three `resources/*` methods stay on the rmcp defaults on purpose: both list methods
    // answer with an empty list, and `resources/read` answers method-not-found. Overriding them
    // to reject uniformly (added 0.2.2, reverted 2026-08-01) turned "this server has none" into a
    // failure, and a failed call makes a model retry with a different `server` argument rather
    // than switch tools — users reported chains of invented server names that the empty list
    // never produced. Do not reintroduce an override without evidence from a released build.
}

#[cfg(test)]
mod tests {
    use super::{FastCtxServer, ServerOptions, SharedRuntime};
    use crate::file_executor::GrepGlobExecutor;
    use crate::search_parallelism::MAX_SEARCH_PARALLELISM;
    use std::sync::Arc;

    #[test]
    fn configured_executor_is_the_server_search_source_for_serial_mid_and_maximum_p() {
        let middle = (MAX_SEARCH_PARALLELISM / 2).max(1);
        for parallelism in [1, middle, MAX_SEARCH_PARALLELISM] {
            let executor = Arc::new(GrepGlobExecutor::with_test_parallelism(parallelism));
            let server = FastCtxServer::with_options_and_executor(
                ServerOptions::default(),
                Arc::clone(&executor),
            );
            assert!(Arc::ptr_eq(&server.grep_glob_executor, &executor));
            assert_eq!(server.grep_glob_executor.parallelism(), parallelism);
            assert_eq!(server.grep_glob_executor.extra_capacity(), parallelism - 1);
        }
    }

    #[test]
    fn connections_share_runtime_resources_but_keep_distinct_session_contexts() {
        let executor = Arc::new(GrepGlobExecutor::with_test_parallelism(1));
        let runtime = SharedRuntime::new(Arc::clone(&executor));
        let first = FastCtxServer::with_session_and_runtime(
            ServerOptions::all(),
            crate::session::SessionContext::library_default(),
            Arc::clone(&runtime),
        );
        let second = FastCtxServer::with_session_and_runtime(
            ServerOptions::all(),
            crate::session::SessionContext::library_default(),
            runtime,
        );

        assert!(Arc::ptr_eq(&first.grep_glob_executor, &executor));
        assert!(Arc::ptr_eq(
            &first.grep_glob_executor,
            &second.grep_glob_executor
        ));
        assert!(Arc::ptr_eq(&first.file_permits, &second.file_permits));
        assert!(Arc::ptr_eq(&first.shell_permits, &second.shell_permits));
        assert!(Arc::ptr_eq(&first.replace_permits, &second.replace_permits));
        assert!(first.replace.shares_locks_with(&second.replace));
        assert!(Arc::ptr_eq(&first.activity, &second.activity));
        assert!(!Arc::ptr_eq(&first.session, &second.session));
    }
}
