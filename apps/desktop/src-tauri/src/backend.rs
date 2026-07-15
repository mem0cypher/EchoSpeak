use serde::Serialize;
use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::PathBuf,
    process::Command as StdCommand,
    sync::{Arc, Mutex},
    time::Duration,
};
use tauri::AppHandle;
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};
use uuid::Uuid;

const MAX_AUTOMATIC_RESTARTS: u32 = 3;
const HEALTH_ATTEMPTS: usize = 360;
const HEALTH_INTERVAL_MS: u64 = 250;
const PROCESS_MONITOR_INTERVAL_MS: u64 = 500;

#[derive(Debug)]
struct Supervisor {
    phase: String,
    detail: String,
    api_base: String,
    api_session_key: String,
    data_dir: PathBuf,
    log_dir: PathBuf,
    child: Option<CommandChild>,
    child_pid: Option<u32>,
    desired_running: bool,
    generation: u64,
    instance_id: String,
    restart_count: u32,
    consecutive_failures: u32,
}

#[derive(Clone)]
pub struct DesktopState {
    inner: Arc<Mutex<Supervisor>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DesktopRuntime {
    pub environment: &'static str,
    pub api_base: String,
    pub api_session_key: String,
    pub backend_phase: String,
    pub backend_detail: String,
    pub backend_pid: Option<u32>,
    pub connection_generation: u64,
    pub instance_id: String,
    pub restart_count: u32,
    pub max_automatic_restarts: u32,
    pub data_dir: String,
    pub log_dir: String,
}

impl DesktopState {
    pub fn new(data_dir: PathBuf, log_dir: PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(&data_dir)
            .map_err(|error| format!("Could not create desktop data directory: {error}"))?;
        std::fs::create_dir_all(&log_dir)
            .map_err(|error| format!("Could not create desktop log directory: {error}"))?;
        let data_dir = data_dir
            .canonicalize()
            .map_err(|error| format!("Could not resolve desktop data directory: {error}"))?;
        let log_dir = log_dir
            .canonicalize()
            .map_err(|error| format!("Could not resolve desktop log directory: {error}"))?;
        let port = reserve_loopback_port()?;
        let api_session_key = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
        Ok(Self {
            inner: Arc::new(Mutex::new(Supervisor {
                phase: "starting".into(),
                detail: "Preparing the local EchoSpeak service".into(),
                api_base: format!("http://127.0.0.1:{port}"),
                api_session_key,
                data_dir,
                log_dir,
                child: None,
                child_pid: None,
                desired_running: true,
                generation: 0,
                instance_id: String::new(),
                restart_count: 0,
                consecutive_failures: 0,
            })),
        })
    }

    pub fn snapshot(&self) -> DesktopRuntime {
        let supervisor = self
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        DesktopRuntime {
            environment: "desktop",
            api_base: supervisor.api_base.clone(),
            api_session_key: supervisor.api_session_key.clone(),
            backend_phase: supervisor.phase.clone(),
            backend_detail: supervisor.detail.clone(),
            backend_pid: supervisor.child_pid,
            connection_generation: supervisor.generation,
            instance_id: supervisor.instance_id.clone(),
            restart_count: supervisor.restart_count,
            max_automatic_restarts: MAX_AUTOMATIC_RESTARTS,
            data_dir: supervisor.data_dir.to_string_lossy().into_owned(),
            log_dir: supervisor.log_dir.to_string_lossy().into_owned(),
        }
    }

    pub fn log_dir(&self) -> PathBuf {
        self.inner
            .lock()
            .expect("desktop supervisor mutex poisoned")
            .log_dir
            .clone()
    }
}

fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("Could not reserve a loopback API port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("Could not inspect the reserved API port: {error}"))
}

fn port_from_api_base(api_base: &str) -> Result<u16, String> {
    api_base
        .rsplit_once(':')
        .and_then(|(_, port)| port.parse::<u16>().ok())
        .ok_or_else(|| "Desktop API base does not contain a valid port".to_string())
}

fn health_ready(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(350)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok()
        && response.starts_with("HTTP/1.1 200")
        && response.contains("\"status\":\"healthy\"")
}

fn product_ready(port: u16, session_key: &str, instance_id: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(750)));
    let request = format!(
        "GET /startup/readiness HTTP/1.1\r\nHost: 127.0.0.1\r\nX-EchoSpeak-Key: {session_key}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() || !response.starts_with("HTTP/1.1 200") {
        return false;
    }
    let Some((_, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    let Ok(payload) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    payload.get("core_ready").and_then(|value| value.as_bool()) == Some(true)
        && payload
            .get("protocol_version")
            .and_then(|value| value.as_str())
            == Some("1")
        && payload.get("instance_id").and_then(|value| value.as_str()) == Some(instance_id)
}

fn request_backend_exit(port: u16, session_key: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(350)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(750)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(750)));
    let body = "{\"delay_seconds\":0}";
    let request = format!(
        "POST /admin/restart HTTP/1.1\r\nHost: 127.0.0.1\r\nX-EchoSpeak-Key: {session_key}\r\nX-Admin-Key: {session_key}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200")
}

#[cfg(windows)]
fn process_is_alive(pid: u32) -> bool {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, WAIT_TIMEOUT},
        System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE},
    };

    unsafe {
        let handle = OpenProcess(PROCESS_SYNCHRONIZE, 0, pid);
        if handle.is_null() {
            return false;
        }
        let result = WaitForSingleObject(handle, 0);
        let _ = CloseHandle(handle);
        result == WAIT_TIMEOUT
    }
}

#[cfg(not(windows))]
fn process_is_alive(_pid: u32) -> bool {
    // The current product target is Windows. Other targets retain the shell
    // termination-event path until they gain an equivalent native probe.
    true
}

pub fn launch_backend(app: AppHandle, state: DesktopState) -> Result<(), String> {
    let (generation, instance_id, api_base, api_session_key, data_dir, log_dir) = {
        let mut supervisor = state
            .inner
            .lock()
            .map_err(|_| "Desktop supervisor lock failed")?;
        if supervisor.child.is_some() {
            return Ok(());
        }
        if !supervisor.desired_running {
            return Err("Backend launch was cancelled".into());
        }
        supervisor.generation += 1;
        supervisor.instance_id = Uuid::new_v4().simple().to_string();
        supervisor.phase = "starting".into();
        supervisor.detail = "Starting the local EchoSpeak service".into();
        (
            supervisor.generation,
            supervisor.instance_id.clone(),
            supervisor.api_base.clone(),
            supervisor.api_session_key.clone(),
            supervisor.data_dir.clone(),
            supervisor.log_dir.clone(),
        )
    };

    let port = port_from_api_base(&api_base)?;
    let parent_pid = std::process::id().to_string();
    let command = app
        .shell()
        .sidecar("echospeak-backend")
        .map_err(|error| format!("Could not locate the packaged EchoSpeak backend: {error}"))?
        .args([
            "--host".to_string(),
            "127.0.0.1".to_string(),
            "--port".to_string(),
            port.to_string(),
            "--parent-pid".to_string(),
            parent_pid,
        ])
        .current_dir(&data_dir)
        .env("ECHOSPEAK_RUNTIME_KIND", "desktop")
        .env("ECHOSPEAK_DESKTOP_INSTANCE_ID", &instance_id)
        .env("ECHOSPEAK_DATA_DIR", &data_dir)
        .env("ECHOSPEAK_LOGS_DIR", &log_dir)
        .env("SOUL_PATH", data_dir.join("SOUL.md"))
        .env("API_HOST", "127.0.0.1")
        .env("API_PORT", port.to_string())
        .env("API_AUTH_ENABLED", "true")
        .env("API_AUTH_LOCALHOST_BYPASS", "false")
        .env("API_AUTH_KEY", &api_session_key)
        .env("ADMIN_API_KEY", &api_session_key)
        .env("API_TRUST_PROXY_HEADERS", "false")
        .env("PYTHONUNBUFFERED", "1");

    let (mut events, child) = command
        .spawn()
        .map_err(|error| format!("Could not start the packaged EchoSpeak backend: {error}"))?;
    let child_pid = child.pid();
    {
        let mut supervisor = state
            .inner
            .lock()
            .map_err(|_| "Desktop supervisor lock failed")?;
        if supervisor.generation != generation || !supervisor.desired_running {
            let _ = child.kill();
            return Err("Backend launch was superseded".into());
        }
        supervisor.child_pid = Some(child_pid);
        supervisor.child = Some(child);
        supervisor.detail = "Waiting for the local EchoSpeak service".into();
    }

    let event_app = app.clone();
    let event_state = state.clone();
    let event_session_key = api_session_key.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    log::info!(target: "echospeak_backend", "{}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Stderr(bytes) => {
                    log::warn!(target: "echospeak_backend", "{}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Error(error) => {
                    log::error!(target: "echospeak_backend", "{error}");
                }
                CommandEvent::Terminated(payload) => {
                    let reason = format!(
                        "Local service exited (code {:?}, signal {:?})",
                        payload.code, payload.signal
                    );
                    let current = {
                        let mut supervisor = event_state
                            .inner
                            .lock()
                            .expect("desktop supervisor mutex poisoned");
                        if supervisor.generation != generation {
                            false
                        } else {
                            supervisor.child = None;
                            supervisor.child_pid = None;
                            supervisor.phase = "recovering".into();
                            supervisor.detail = reason.clone();
                            supervisor.desired_running
                        }
                    };
                    if current {
                        // PyInstaller one-file mode has a bootloader parent and
                        // a worker child. If only the tracked bootloader exits,
                        // ask the still-healthy authenticated worker to exit so
                        // recovery cannot create a competing orphan service.
                        if health_ready(port) {
                            let _ = request_backend_exit(port, &event_session_key);
                        }
                        schedule_restart(event_app.clone(), event_state.clone(), reason);
                    }
                    return;
                }
                _ => {}
            }
        }
    });

    // PyInstaller one-file mode keeps stdout/stderr owned by its worker. If the
    // tracked bootloader alone crashes, the shell event channel therefore stays
    // open. Monitor the process handle independently so the host can retire the
    // authenticated worker before it starts a replacement generation.
    let monitor_app = app.clone();
    let monitor_state = state.clone();
    let monitor_session_key = api_session_key.clone();
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_millis(PROCESS_MONITOR_INTERVAL_MS)).await;
            let still_current = {
                let supervisor = monitor_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                supervisor.generation == generation
                    && supervisor.desired_running
                    && supervisor.child_pid == Some(child_pid)
            };
            if !still_current {
                return;
            }
            if process_is_alive(child_pid) {
                continue;
            }

            let reason = format!("Tracked local service process {child_pid} exited unexpectedly");
            let Some(child) = take_failed_generation(&monitor_state, generation, &reason) else {
                return;
            };

            // Invalidating the generation first makes a later shell event inert,
            // so one crash consumes one recovery attempt. The worker also watches
            // this bootloader, while the authenticated route is a healthy-worker
            // fallback. Allow either path to release the port before replacement.
            if product_ready(port, &api_session_key, &instance_id) {
                let _ = request_backend_exit(port, &monitor_session_key);
            }
            tokio::time::sleep(Duration::from_millis(750)).await;
            if health_ready(port) {
                let _ = request_backend_exit(port, &monitor_session_key);
                tokio::time::sleep(Duration::from_millis(750)).await;
            }
            if let Some(child) = child {
                terminate_process_tree(child);
            }
            if health_ready(port) {
                let mut supervisor = monitor_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                supervisor.phase = "failed".into();
                supervisor.detail = format!(
                    "{reason}. The prior authenticated worker could not be retired; replacement was blocked."
                );
                return;
            }
            schedule_restart(monitor_app, monitor_state, reason);
            return;
        }
    });

    let health_app = app;
    let health_state = state;
    tauri::async_runtime::spawn(async move {
        for _ in 0..HEALTH_ATTEMPTS {
            let still_current = {
                let supervisor = health_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                supervisor.generation == generation && supervisor.desired_running
            };
            if !still_current {
                return;
            }
            if health_ready(port) {
                let mut supervisor = health_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                if supervisor.generation == generation {
                    supervisor.phase = "ready".into();
                    supervisor.detail = "Local service is ready".into();
                    supervisor.consecutive_failures = 0;
                }
                return;
            }
            tokio::time::sleep(Duration::from_millis(HEALTH_INTERVAL_MS)).await;
        }
        fail_generation(
            health_app,
            health_state,
            generation,
            "Local service did not become healthy within 90 seconds".into(),
        );
    });

    Ok(())
}

fn fail_generation(app: AppHandle, state: DesktopState, generation: u64, reason: String) {
    let Some(child) = take_failed_generation(&state, generation, &reason) else {
        return;
    };
    if let Some(child) = child {
        terminate_process_tree(child);
    }
    schedule_restart(app, state, reason);
}

fn take_failed_generation(
    state: &DesktopState,
    generation: u64,
    reason: &str,
) -> Option<Option<CommandChild>> {
    let mut supervisor = state
        .inner
        .lock()
        .expect("desktop supervisor mutex poisoned");
    if supervisor.generation != generation || !supervisor.desired_running {
        return None;
    }
    supervisor.generation += 1;
    supervisor.phase = "recovering".into();
    supervisor.detail = reason.into();
    supervisor.child_pid = None;
    Some(supervisor.child.take())
}

fn schedule_restart(app: AppHandle, state: DesktopState, reason: String) {
    let delay_ms = {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        if !supervisor.desired_running {
            return;
        }
        if supervisor.consecutive_failures >= MAX_AUTOMATIC_RESTARTS {
            supervisor.phase = "failed".into();
            supervisor.detail = format!("{reason}. Automatic recovery is exhausted.");
            return;
        }
        supervisor.consecutive_failures += 1;
        supervisor.restart_count += 1;
        supervisor.phase = "recovering".into();
        supervisor.detail = format!(
            "{reason}. Recovery attempt {}/{} is queued.",
            supervisor.consecutive_failures, MAX_AUTOMATIC_RESTARTS
        );
        500_u64 * (1_u64 << (supervisor.consecutive_failures - 1))
    };

    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(delay_ms)).await;
        if let Err(error) = launch_backend(app.clone(), state.clone()) {
            log::error!("Desktop backend recovery failed: {error}");
            schedule_restart(app, state, error);
        }
    });
}

pub fn recover_from_launch_failure(app: AppHandle, state: DesktopState, reason: String) {
    {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        supervisor.phase = "recovering".into();
        supervisor.detail = reason.clone();
        supervisor.child = None;
        supervisor.child_pid = None;
    }
    schedule_restart(app, state, reason);
}

pub fn restart_backend(app: AppHandle, state: DesktopState) -> DesktopRuntime {
    let (child, orphan_endpoint) = {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        supervisor.desired_running = true;
        supervisor.generation += 1;
        supervisor.consecutive_failures = 0;
        supervisor.phase = "recovering".into();
        supervisor.detail = "Restarting the local EchoSpeak service".into();
        supervisor.child_pid = None;
        (
            supervisor.child.take(),
            port_from_api_base(&supervisor.api_base)
                .ok()
                .map(|port| (port, supervisor.api_session_key.clone())),
        )
    };
    if let Some(child) = child {
        terminate_process_tree(child);
    } else if let Some((port, session_key)) = orphan_endpoint {
        if health_ready(port) && request_backend_exit(port, &session_key) {
            let delayed_app = app.clone();
            let delayed_state = state.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_millis(750)).await;
                if let Err(error) = launch_backend(delayed_app.clone(), delayed_state.clone()) {
                    schedule_restart(delayed_app, delayed_state, error);
                }
            });
            return state.snapshot();
        }
    }
    if let Err(error) = launch_backend(app.clone(), state.clone()) {
        schedule_restart(app, state.clone(), error);
    }
    state.snapshot()
}

pub fn shutdown_backend(state: &DesktopState) {
    let (child, orphan_endpoint) = {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        supervisor.desired_running = false;
        supervisor.generation += 1;
        supervisor.phase = "stopped".into();
        supervisor.detail = "Desktop host is closing".into();
        supervisor.child_pid = None;
        (
            supervisor.child.take(),
            port_from_api_base(&supervisor.api_base)
                .ok()
                .map(|port| (port, supervisor.api_session_key.clone())),
        )
    };
    if let Some(child) = child {
        terminate_process_tree(child);
    } else if let Some((port, session_key)) = orphan_endpoint {
        if health_ready(port) {
            let _ = request_backend_exit(port, &session_key);
        }
    }
}

fn terminate_process_tree(child: CommandChild) {
    let pid = child.pid();
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let _ = StdCommand::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
    let _ = child.kill();
}
