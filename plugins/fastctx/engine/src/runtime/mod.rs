//! Thin stdio proxy and the per-user, per-build FastCtx control center.

pub(crate) mod activity;
mod local_ipc;
mod protocol;
#[cfg(windows)]
mod windows_process;

use crate::control::paths::ControlPaths;
use crate::file_executor::GrepGlobExecutor;
use crate::server::{FastCtxServer, ServerOptions, SharedRuntime};
use crate::session::{SessionContext, SessionEnvironment};
use fs2::FileExt;
use local_ipc::{BoxedStream, Listener, LocalEndpoint};
use rmcp::ServiceExt;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::path::Path;
use std::process::{Command, ExitCode, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use tokio::io::{AsyncWriteExt, split};
use tokio::sync::OnceCell;
use tokio_util::sync::CancellationToken;

/// How long a proxy waits for the control center before falling back to a standalone server.
pub(crate) const STARTUP_TIMEOUT: Duration = Duration::from_secs(10);
const CONNECT_RETRY: Duration = Duration::from_millis(20);
const ACCEPT_RETRY: Duration = Duration::from_secs(1);
const DEFAULT_IDLE_TIMEOUT: Duration = Duration::from_secs(10 * 60);
const DEFAULT_MAINTENANCE_INTERVAL: Duration = Duration::from_secs(60);
const SERVICE_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);
/// Backstop for a closing proxy waiting on answers the control center already owes. A session that
/// ends normally never reaches it: the control center closes the connection once it has answered.
const RESPONSE_DRAIN_TIMEOUT: Duration = Duration::from_secs(5);
/// How long work in progress may keep running after its client stopped reading. Long enough for a
/// finished handler to write its answer, short enough that nothing outlives the session by much.
const INPUT_CLOSED_GRACE: Duration = Duration::from_millis(250);
#[cfg(unix)]
const MAX_UNIX_SOCKET_PATH_BYTES: usize = 100;

/// Captures the thin proxy's native state without loading settings or any heavy executor.
pub(crate) fn capture_proxy_environment() -> Result<SessionEnvironment, String> {
    SessionEnvironment::capture()
}

/// Connects to the matching control center or starts exactly one before MCP stdin is consumed.
pub(crate) async fn connect_or_start(
    options: ServerOptions,
    environment: &SessionEnvironment,
) -> Result<BoxedStream, String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("Cannot locate the running fastctx binary: {error}"))?;
    let endpoint = endpoint_for(environment)?;
    crate::edit::private_storage::ensure_private_directory(
        endpoint.runtime_directory(),
        "control-center runtime",
    )?;

    if let Ok(mut stream) = local_ipc::connect(&endpoint).await {
        establish(&mut stream, options, environment.clone()).await?;
        return Ok(stream);
    }

    let startup_lock = crate::edit::private_storage::open_lock_file(
        &endpoint.startup_lock_path(),
        "control-center startup lock",
    )?;
    acquire_startup_lock(&startup_lock, &endpoint.startup_lock_path()).await?;

    if let Ok(mut stream) = local_ipc::connect(&endpoint).await {
        establish(&mut stream, options, environment.clone()).await?;
        return Ok(stream);
    }

    spawn_bootstrap(&executable, environment)?;
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    loop {
        match local_ipc::connect(&endpoint).await {
            Ok(mut stream) => {
                establish(&mut stream, options, environment.clone()).await?;
                return Ok(stream);
            }
            Err(_) if Instant::now() < deadline => {
                tokio::time::sleep(CONNECT_RETRY).await;
            }
            Err(error) => {
                return Err(format!(
                    "The FastCtx control center did not become ready within 10 seconds: {error}"
                ));
            }
        }
    }
}

async fn establish(
    stream: &mut BoxedStream,
    options: ServerOptions,
    environment: SessionEnvironment,
) -> Result<(), String> {
    tokio::time::timeout(STARTUP_TIMEOUT, async {
        protocol::write_handshake(stream, &protocol::Handshake::new(options, environment)).await?;
        protocol::read_handshake_response(stream).await
    })
    .await
    .map_err(|_| {
        "Timed out waiting for the FastCtx control center to accept the session handshake."
            .to_string()
    })?
}

async fn acquire_startup_lock(file: &File, path: &Path) -> Result<(), String> {
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    loop {
        match file.try_lock_exclusive() {
            Ok(()) => return Ok(()),
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < deadline =>
            {
                tokio::time::sleep(CONNECT_RETRY).await;
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                return Err(
                    "Timed out waiting for another FastCtx proxy to finish starting the control center."
                        .to_string(),
                );
            }
            Err(error) => {
                return Err(format!(
                    "Cannot lock the control-center startup gate {}: {error}",
                    crate::paths::display_path(path)
                ));
            }
        }
    }
}

fn endpoint_for(environment: &SessionEnvironment) -> Result<LocalEndpoint, String> {
    let home = environment
        .var_os("HOME")
        .filter(|value| !value.is_empty())
        .or_else(|| {
            environment
                .var_os("USERPROFILE")
                .filter(|value| !value.is_empty())
        })
        .ok_or_else(|| {
            "Cannot determine the user home directory for the FastCtx control center. Set HOME or USERPROFILE and retry."
                .to_string()
        })?;
    let home_hash = short_hash(&crate::session::native_bytes(&home), 12);
    let build_id = effective_build_id(environment);
    let id = format!("fastctx-engine-{home_hash}-{build_id}");
    let preferred_runtime_directory = crate::edit::private_storage::control_center_directory();
    #[cfg(unix)]
    let runtime_directory = select_unix_runtime_directory(
        preferred_runtime_directory,
        crate::edit::private_storage::short_control_center_directory(),
        &id,
    )?;
    #[cfg(not(unix))]
    let runtime_directory = preferred_runtime_directory;
    Ok(LocalEndpoint::new(runtime_directory, id))
}

#[cfg(unix)]
fn select_unix_runtime_directory(
    preferred: std::path::PathBuf,
    fallback: std::path::PathBuf,
    id: &str,
) -> Result<std::path::PathBuf, String> {
    use std::os::unix::ffi::OsStrExt;

    let socket_length = |directory: &Path| {
        directory
            .join(format!("{id}.sock"))
            .as_os_str()
            .as_bytes()
            .len()
            .saturating_add(1)
    };
    if socket_length(&preferred) <= MAX_UNIX_SOCKET_PATH_BYTES {
        return Ok(preferred);
    }
    // Darwin's per-user temporary directory can already consume most of sockaddr_un::sun_path.
    // Keep the endpoint and both ownership locks together in an owner-only short directory.
    let fallback_length = socket_length(&fallback);
    if fallback_length <= MAX_UNIX_SOCKET_PATH_BYTES {
        return Ok(fallback);
    }
    Err(format!(
        "The private control-center socket path is too long ({fallback_length} bytes): {}",
        crate::paths::display_path(&fallback.join(format!("{id}.sock")))
    ))
}

fn short_hash(bytes: &[u8], characters: usize) -> String {
    let digest = Sha256::digest(bytes);
    hex::encode(digest)[..characters].to_string()
}

fn effective_build_id(_environment: &SessionEnvironment) -> String {
    #[cfg(debug_assertions)]
    if let Ok(override_id) = _environment.var("FASTCTX_TEST_BUILD_ID")
        && !override_id.is_empty()
        && override_id.len() <= 32
        && override_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return override_id;
    }
    env!("FASTCTX_BUILD_ID").to_string()
}

fn spawn_bootstrap(executable: &Path, environment: &SessionEnvironment) -> Result<(), String> {
    let mut command = Command::new(executable);
    environment.configure_command(&mut command);
    command
        .arg("runtime-bootstrap")
        .current_dir(environment.cwd())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(crate::process_policy::noninteractive_creation_flags(0));
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot start the FastCtx control-center bootstrap: {error}"))
}

/// Intermediate child that reparents the long-lived control center before the proxy returns.
pub(crate) fn run_bootstrap_entry() -> Result<(), String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("Cannot locate the control-center binary: {error}"))?;
    let environment = SessionEnvironment::capture()?;
    let mut command = Command::new(&executable);
    environment.configure_command(&mut command);
    command
        .arg("runtime-host")
        .current_dir(environment.cwd())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // SAFETY: pre_exec performs only the async-signal-safe setsid syscall.
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() < 0 {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(())
                }
            });
        }
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::System::Threading::{
            CREATE_BREAKAWAY_FROM_JOB, CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS,
        };
        let detached = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP;
        match windows_process::spawn_without_inherited_handles(
            &executable,
            environment.cwd(),
            detached | CREATE_BREAKAWAY_FROM_JOB,
        ) {
            Ok(_) => Ok(()),
            Err(error) if error.raw_os_error() == Some(5) => {
                windows_process::spawn_without_inherited_handles(
                    &executable,
                    environment.cwd(),
                    detached,
                )
                .map_err(|error| format!("Cannot detach the FastCtx control center: {error}"))
            }
            Err(error) => Err(format!("Cannot detach the FastCtx control center: {error}")),
        }
    }
    #[cfg(not(windows))]
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Cannot detach the FastCtx control center: {error}"))
}

struct HostState {
    runtime: OnceCell<Arc<SharedRuntime>>,
    control_paths: OnceCell<ControlPaths>,
    activity: Arc<activity::RuntimeActivity>,
}

impl HostState {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            runtime: OnceCell::new(),
            control_paths: OnceCell::new(),
            activity: activity::RuntimeActivity::new(),
        })
    }

    async fn runtime_for(
        &self,
        session: &Arc<SessionContext>,
    ) -> Result<Arc<SharedRuntime>, String> {
        let runtime = self
            .runtime
            .get_or_try_init(|| async {
                let parallelism = session.settings.search_parallelism().map_err(|error| {
                    format!(
                        "Cannot start the MCP session with settings from {}: {error}. Repair the value and retry.",
                        crate::paths::display_path(&session.control_paths.fastctx_config)
                    )
                })?;
                let executor = Arc::new(GrepGlobExecutor::with_parallelism(parallelism.effective));
                Ok::<_, String>(SharedRuntime::with_activity(
                    executor,
                    Arc::clone(&self.activity),
                ))
            })
            .await?;
        let _ = self.control_paths.set(session.control_paths.clone());
        Ok(Arc::clone(runtime))
    }
}

/// Final detached control-center entry point.
pub(crate) async fn run_host_entry(
    idle_timeout_ms: Option<u64>,
    maintenance_interval_ms: Option<u64>,
) -> Result<(), String> {
    let environment = SessionEnvironment::capture()?;
    let endpoint = endpoint_for(&environment)?;
    crate::edit::private_storage::ensure_private_directory(
        endpoint.runtime_directory(),
        "control-center runtime",
    )?;
    let instance_lock = crate::edit::private_storage::open_lock_file(
        &endpoint.instance_lock_path(),
        "control-center instance lock",
    )?;
    match instance_lock.try_lock_exclusive() {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return Ok(()),
        Err(error) => {
            return Err(format!(
                "Cannot lock the control-center instance gate: {error}"
            ));
        }
    }
    let mut listener = Listener::bind(&endpoint)?;
    #[cfg(debug_assertions)]
    record_test_host_start(&environment);
    let state = HostState::new();
    let shutdown = CancellationToken::new();
    let idle_timeout = idle_timeout_ms
        .or_else(|| duration_override(&environment, "FASTCTX_TEST_RUNTIME_IDLE_MS"))
        .map(Duration::from_millis)
        .unwrap_or(DEFAULT_IDLE_TIMEOUT);
    let (idle_candidate_tx, mut idle_candidate_rx) = tokio::sync::mpsc::channel(1);
    let monitor = tokio::spawn(monitor_idle(
        Arc::clone(&state),
        shutdown.clone(),
        idle_timeout,
        idle_candidate_tx,
    ));
    let maintenance = tokio::spawn(monitor_maintenance(
        Arc::clone(&state),
        shutdown.clone(),
        maintenance_interval_ms
            .or_else(|| duration_override(&environment, "FASTCTX_TEST_RUNTIME_MAINTENANCE_MS"))
            .map(Duration::from_millis)
            .unwrap_or(DEFAULT_MAINTENANCE_INTERVAL),
    ));
    let mut connections = tokio::task::JoinSet::new();

    loop {
        tokio::select! {
            accepted = listener.accept() => {
                match accepted {
                    Ok(stream) => {
                        let Some(connection) = state.activity.try_connection() else {
                            drop(stream);
                            continue;
                        };
                        connections.spawn(serve_connection(
                            stream,
                            Arc::clone(&state),
                            shutdown.clone(),
                            connection,
                        ));
                    }
                    Err(error) => {
                        eprintln!("fastctx control center: {error}; retrying.");
                        tokio::select! {
                            () = shutdown.cancelled() => break,
                            () = tokio::time::sleep(ACCEPT_RETRY) => {}
                        }
                    }
                }
            }
            Some(()) = idle_candidate_rx.recv() => {
                if state.activity.try_begin_shutdown(idle_timeout) {
                    shutdown.cancel();
                    break;
                }
            }
            () = shutdown.cancelled() => break,
            result = connections.join_next(), if !connections.is_empty() => {
                if let Some(Err(error)) = result {
                    eprintln!("fastctx control center: connection task failed: {error}");
                }
            }
        }
    }

    shutdown.cancel();
    let deadline = tokio::time::sleep(SERVICE_SHUTDOWN_TIMEOUT);
    tokio::pin!(deadline);
    // Drain only while connections remain. The endpoint stays bound until this function returns,
    // and a proxy that connects during the wait is never accepted: it stalls, then degrades to a
    // standalone server. An always-enabled deadline branch would make every exit wait in full.
    while !connections.is_empty() {
        tokio::select! {
            _ = &mut deadline => {
                connections.abort_all();
                while connections.join_next().await.is_some() {}
                break;
            }
            result = connections.join_next() => {
                if result.is_none() {
                    break;
                }
            }
        }
    }
    monitor.abort();
    let _ = monitor.await;
    maintenance.abort();
    let _ = maintenance.await;
    drop(instance_lock);
    Ok(())
}

/// Reads a millisecond timer override for the control center's own loops.
///
/// Deliberately honoured in every profile. While this was `debug_assertions`-only, one release
/// test run left sixty-five control centers holding the production ten-minute timeout, which is
/// the very process pile-up this runtime exists to remove. Both timers only shorten the host's
/// own life, so a value from the environment cannot outlive or override a caller's session.
fn duration_override(environment: &SessionEnvironment, name: &str) -> Option<u64> {
    environment.var(name).ok()?.parse::<u64>().ok()
}

#[cfg(debug_assertions)]
fn record_test_host_start(environment: &SessionEnvironment) {
    use std::io::Write as _;
    use std::path::PathBuf;
    let Some(path) = environment.var_os("FASTCTX_TEST_RUNTIME_EVENT_LOG") else {
        return;
    };
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    if let Ok(mut file) = options.open(PathBuf::from(path)) {
        let _ = writeln!(file, "START {}", std::process::id());
    }
}

async fn monitor_maintenance(
    state: Arc<HostState>,
    shutdown: CancellationToken,
    interval: Duration,
) {
    loop {
        tokio::select! {
            () = shutdown.cancelled() => return,
            () = tokio::time::sleep(interval) => {}
        }
        let Some(paths) = state.control_paths.get().cloned() else {
            continue;
        };
        match tokio::task::spawn_blocking(move || crate::shell::jobs::reap_history(&paths)).await {
            Ok(Ok(_)) => {}
            Ok(Err(error)) => {
                eprintln!("fastctx control center: periodic background-job cleanup failed: {error}")
            }
            Err(error) => eprintln!(
                "fastctx control center: periodic background-job cleanup task failed: {error}"
            ),
        }
    }
}

async fn serve_connection(
    mut stream: BoxedStream,
    state: Arc<HostState>,
    shutdown: CancellationToken,
    _connection: activity::ConnectionActivityGuard,
) {
    let handshake = match protocol::read_handshake(&mut stream).await {
        Ok(handshake) => handshake,
        Err(error) => {
            let _ = protocol::write_handshake_error(&mut stream, error).await;
            return;
        }
    };
    let session = match SessionContext::from_environment(handshake.environment) {
        Ok(session) => session,
        Err(error) => {
            let _ = protocol::write_handshake_error(&mut stream, error).await;
            return;
        }
    };
    let runtime = match state.runtime_for(&session).await {
        Ok(runtime) => runtime,
        Err(error) => {
            let _ = protocol::write_handshake_error(&mut stream, error).await;
            return;
        }
    };
    if protocol::write_handshake_success(&mut stream)
        .await
        .is_err()
    {
        return;
    }
    let (mut reader, writer) = split(stream);
    // Requests arrive framed so the proxy can mark the end of input; the MCP server reads the
    // plain stream that comes back out, and the sink closing is what it sees as EOF.
    let (requests, mut request_sink) = tokio::io::simplex(protocol::MAX_REQUEST_FRAME_BYTES);
    let mut unframe = tokio::spawn(async move {
        if let Err(error) = protocol::receive_requests(&mut reader, &mut request_sink).await {
            eprintln!("fastctx control center: {error}");
        }
        drop(request_sink);
    });
    let service = match FastCtxServer::with_session_and_runtime(handshake.options, session, runtime)
        .serve((requests, writer))
        .await
    {
        Ok(service) => service,
        Err(error) => {
            eprintln!("fastctx control center: cannot start MCP connection: {error}");
            unframe.abort();
            return;
        }
    };
    let cancellation = service.cancellation_token();
    let mut waiting = tokio::spawn(service.waiting());
    tokio::select! {
        _ = shutdown.cancelled() => {
            cancellation.cancel();
            end_service(&mut waiting).await;
        }
        _ = &mut unframe => {
            // The client is gone. Work that already finished still gets its answer written, but
            // an MCP server left holding the transport would keep running a request nobody can
            // read — the reason a closed stdin has to end foreground work rather than outlive it.
            if tokio::time::timeout(INPUT_CLOSED_GRACE, &mut waiting).await.is_err() {
                cancellation.cancel();
                end_service(&mut waiting).await;
            }
        }
        _ = &mut waiting => {}
    }
    unframe.abort();
}

/// Waits out a cancelled MCP service, then stops waiting.
async fn end_service<T>(waiting: &mut tokio::task::JoinHandle<T>) {
    if tokio::time::timeout(SERVICE_SHUTDOWN_TIMEOUT, &mut *waiting)
        .await
        .is_err()
    {
        waiting.abort();
        let _ = waiting.await;
    }
}

async fn monitor_idle(
    state: Arc<HostState>,
    shutdown: CancellationToken,
    idle_timeout: Duration,
    candidate: tokio::sync::mpsc::Sender<()>,
) {
    let interval = idle_timeout
        .div_f64(4.0)
        .clamp(Duration::from_millis(50), Duration::from_secs(30));
    // First instant of the current unbroken run of registry-scan failures, if any.
    let mut scan_failing_since: Option<Instant> = None;
    loop {
        tokio::select! {
            () = shutdown.cancelled() => return,
            () = tokio::time::sleep(interval) => {}
        }
        if !state.activity.is_shutdown_eligible(idle_timeout) {
            scan_failing_since = None;
            continue;
        }
        let Some(paths) = state.control_paths.get().cloned() else {
            if candidate.send(()).await.is_err() {
                return;
            }
            continue;
        };
        let running = tokio::task::spawn_blocking(move || {
            crate::shell::jobs::running_summaries(&paths).map(|jobs| !jobs.is_empty())
        })
        .await
        .unwrap_or_else(|error| Err(format!("the inspection task failed: {error}")));
        match running {
            Ok(false) => {
                scan_failing_since = None;
                // A request may have arrived while the registry scan ran off-thread.
                if !state.activity.is_shutdown_eligible(idle_timeout) {
                    continue;
                }
                if candidate.send(()).await.is_err() {
                    return;
                }
            }
            Ok(true) => scan_failing_since = None,
            Err(error) => {
                eprintln!(
                    "fastctx control center: cannot inspect running jobs for idle shutdown: {error}"
                );
                // Fail open once scans have failed for a full idle window: one damaged registry
                // record fails every scan, and a host that insists on a clean scan before exiting
                // would never exit. Exiting is safe — job supervisors are detached processes and
                // the next connection bootstraps a fresh host that reads the same on-disk registry.
                let failing_since = *scan_failing_since.get_or_insert_with(Instant::now);
                if failing_since.elapsed() >= idle_timeout {
                    eprintln!(
                        "fastctx control center: registry scans kept failing for a full idle window; shutting down anyway."
                    );
                    if candidate.send(()).await.is_err() {
                        return;
                    }
                }
            }
        }
    }
}

/// Why a proxy session stopped pumping bytes.
enum ProxyStop {
    /// stdin reached EOF. No further request can arrive, but answers already owed are still due.
    InputClosed,
    /// The session ends now, with nothing left to deliver.
    Immediate,
}

/// Pumps MCP bytes after the handshake. Any post-establishment failure exits without fallback.
pub(crate) async fn forward_stdio(
    stream: BoxedStream,
    parent: Option<Option<crate::process_identity::ProcessIdentity>>,
) -> Result<ExitCode, String> {
    let stdin = crate::stdio_transport::DetachedStdin::start()?;
    let stdin_eof = stdin.eof_token();
    let stdin_error = stdin.read_error_receiver();
    let (mut reader, mut writer) = split(stream);
    let mut stdout = tokio::io::stdout();
    let forwarded = Arc::new(AtomicBool::new(false));
    let upload_forwarded = Arc::clone(&forwarded);
    let mut upload = tokio::spawn(async move {
        // The end-of-input frame, not the socket state, is what tells the control center that no
        // further request is coming; closing the write direction only releases it where the
        // transport supports that.
        let result =
            protocol::forward_requests(&mut { stdin }, &mut writer, &upload_forwarded).await;
        let shutdown = writer.shutdown().await;
        result.and(shutdown)
    });
    let mut download = tokio::spawn(async move {
        tokio::io::copy(&mut reader, &mut stdout).await?;
        stdout.flush().await
    });

    let monitor_stop = Arc::new(AtomicBool::new(false));
    let (parent_exit, monitor) = parent_exit_monitor(parent, Arc::clone(&monitor_stop));
    tokio::pin!(parent_exit);
    let stdin_error_wait = wait_for_stdin_error(stdin_error.clone());
    tokio::pin!(stdin_error_wait);

    let stop = tokio::select! {
        biased;
        error = &mut stdin_error_wait => Err(error),
        () = stdin_eof.cancelled() => Ok(ProxyStop::InputClosed),
        () = &mut parent_exit => Ok(ProxyStop::Immediate),
        () = wait_for_termination_signal() => Ok(ProxyStop::Immediate),
        result = &mut upload => match stdin_error.borrow().clone() {
            Some(error) => Err(error),
            None => match result {
                Ok(Ok(_)) => Ok(ProxyStop::InputClosed),
                Ok(Err(error)) => Err(format!("Cannot forward MCP stdin to the FastCtx control center: {error}")),
                Err(error) => Err(format!("The FastCtx control-center input task failed: {error}")),
            }
        },
        result = &mut download => match result {
            Ok(Ok(_)) => Err(
                "The FastCtx control-center connection closed unexpectedly; the in-flight request was not replayed."
                    .to_string(),
            ),
            Ok(Err(error)) => Err(format!("The FastCtx control-center connection failed: {error}")),
            Err(error) => Err(format!("The FastCtx control-center output task failed: {error}")),
        },
    };

    let result = match stop {
        Err(error) => Err(error),
        Ok(ProxyStop::Immediate) => Ok(()),
        // Nothing was ever asked, so nothing can be owed; waiting would only stall a client that
        // opened the transport and changed its mind.
        Ok(ProxyStop::InputClosed) if !forwarded.load(Ordering::Acquire) => Ok(()),
        Ok(ProxyStop::InputClosed) => drain_owed_answers(&mut download, &mut parent_exit).await,
    };
    upload.abort();
    download.abort();
    monitor_stop.store(true, Ordering::Release);
    if let Some(monitor) = monitor {
        let _ = monitor.await;
    }
    result?;
    Ok(ExitCode::SUCCESS)
}

/// Delivers the answers the control center still owes once stdin has closed.
///
/// A stdio proxy that abandoned the connection at EOF would report success while silently
/// discarding responses to requests it had already forwarded — `initialize | tools/list | close`
/// came back empty. Unix half-closes the socket, so the control center sees the end of input and
/// closes as soon as it is done; Windows named pipes have no half-close, so sessions that end this
/// way wait out the bound instead. Codex ends MCP children by signal rather than by closing stdin,
/// so only scripted clients and diagnostics ever pay it.
async fn drain_owed_answers(
    download: &mut tokio::task::JoinHandle<std::io::Result<()>>,
    parent_exit: &mut (impl std::future::Future<Output = ()> + Unpin),
) -> Result<(), String> {
    tokio::select! {
        biased;
        () = parent_exit => Ok(()),
        () = wait_for_termination_signal() => Ok(()),
        drained = tokio::time::timeout(RESPONSE_DRAIN_TIMEOUT, download) => match drained {
            // After EOF the control center closing the connection is the clean end of a session.
            Ok(Ok(Ok(()))) => Ok(()),
            Ok(Ok(Err(error))) => Err(format!("The FastCtx control-center connection failed: {error}")),
            Ok(Err(error)) => Err(format!("The FastCtx control-center output task failed: {error}")),
            // A control center still busy with cancelled work does not hold a closing session open.
            Err(_) => Ok(()),
        },
    }
}

type ParentExitFuture = std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send>>;
type ParentExitMonitor = (ParentExitFuture, Option<tokio::task::JoinHandle<()>>);

fn parent_exit_monitor(
    parent: Option<Option<crate::process_identity::ProcessIdentity>>,
    stop: Arc<AtomicBool>,
) -> ParentExitMonitor {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let monitor = match parent {
        None => None,
        Some(None) => {
            let _ = sender.send(());
            return (Box::pin(async {}), None);
        }
        Some(Some(identity)) => Some(tokio::task::spawn_blocking(move || {
            if crate::process_identity::wait_for_identity_exit_until(&identity, &stop) {
                let _ = sender.send(());
            }
        })),
    };
    let future = async move {
        match receiver.await {
            Ok(()) => {}
            Err(_) => std::future::pending::<()>().await,
        }
    };
    (Box::pin(future), monitor)
}

async fn wait_for_stdin_error(
    mut receiver: tokio::sync::watch::Receiver<Option<String>>,
) -> String {
    loop {
        if let Some(error) = receiver.borrow().clone() {
            return error;
        }
        if receiver.changed().await.is_err() {
            return std::future::pending::<String>().await;
        }
    }
}

#[cfg(unix)]
async fn wait_for_termination_signal() {
    use tokio::signal::unix::{SignalKind, signal};
    let Ok(mut terminate) = signal(SignalKind::terminate()) else {
        return std::future::pending::<()>().await;
    };
    let Ok(mut interrupt) = signal(SignalKind::interrupt()) else {
        return std::future::pending::<()>().await;
    };
    tokio::select! {
        _ = terminate.recv() => {}
        _ = interrupt.recv() => {}
    }
}

#[cfg(not(unix))]
async fn wait_for_termination_signal() {
    std::future::pending::<()>().await
}

#[cfg(test)]
mod tests {
    use super::short_hash;

    #[test]
    fn endpoint_hashes_are_stable_and_separate_inputs() {
        assert_eq!(short_hash(b"home-a", 12), short_hash(b"home-a", 12));
        assert_ne!(short_hash(b"home-a", 12), short_hash(b"home-b", 12));
    }

    #[cfg(unix)]
    #[test]
    fn long_unix_runtime_paths_fall_back_to_the_short_private_directory() {
        use super::select_unix_runtime_directory;
        use std::path::PathBuf;

        let preferred = PathBuf::from("/").join("long".repeat(30));
        let fallback = PathBuf::from("/tmp/fastctx-engine-1000");
        let selected = select_unix_runtime_directory(
            preferred,
            fallback.clone(),
            "fastctx-engine-0123456789ab-0123456789abcdef",
        )
        .unwrap();
        assert_eq!(selected, fallback);
    }
}
