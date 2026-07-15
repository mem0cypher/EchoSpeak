mod backend;

use backend::{DesktopRuntime, DesktopState};
use tauri::{Manager, State, Window};
use tauri_plugin_log::{Target, TargetKind};

#[tauri::command]
fn desktop_runtime(state: State<'_, DesktopState>) -> DesktopRuntime {
    state.snapshot()
}

#[tauri::command]
fn restart_desktop_backend(
    app: tauri::AppHandle,
    state: State<'_, DesktopState>,
) -> DesktopRuntime {
    backend::restart_backend(app, state.inner().clone())
}

#[tauri::command]
async fn pick_project_folder() -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        Ok(rfd::FileDialog::new()
            .set_title("Attach a Project folder to EchoSpeak")
            .pick_folder()
            .map(|path| path.to_string_lossy().into_owned()))
    })
    .await
    .map_err(|error| format!("Folder picker failed: {error}"))?
}

#[tauri::command]
fn open_desktop_logs(state: State<'_, DesktopState>) -> Result<(), String> {
    let path = state.log_dir();
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        std::process::Command::new("explorer")
            .arg(&path)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|error| format!("Could not open the desktop log directory: {error}"))?;
        Ok(())
    }
    #[cfg(not(windows))]
    Err(format!(
        "Open this log directory manually: {}",
        path.display()
    ))
}

#[tauri::command]
fn control_desktop_window(action: String, window: Window) -> Result<(), String> {
    match action.as_str() {
        "minimize" => window.minimize(),
        "toggle_maximize" => {
            if window.is_maximized().map_err(|error| error.to_string())? {
                window.unmaximize()
            } else {
                window.maximize()
            }
        }
        "close" => window.close(),
        _ => return Err(format!("Unsupported desktop window action: {action}")),
    }
    .map_err(|error| error.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut log_builder = tauri_plugin_log::Builder::new()
        .level(log::LevelFilter::Info)
        .max_file_size(5_000_000);
    if let Some(path) = std::env::var_os("ECHOSPEAK_DESKTOP_LOG_DIR")
        .filter(|value| !value.is_empty())
        .map(std::path::PathBuf::from)
    {
        log_builder = log_builder.targets([
            Target::new(TargetKind::Stdout),
            Target::new(TargetKind::Folder {
                path,
                file_name: None,
            }),
        ]);
    }

    let app = tauri::Builder::default()
        // Single-instance must initialize first so a second launch only focuses this window.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(log_builder.build())
        .invoke_handler(tauri::generate_handler![
            desktop_runtime,
            restart_desktop_backend,
            pick_project_folder,
            open_desktop_logs,
            control_desktop_window
        ])
        .setup(|app| {
            // Host-controlled override supports disposable development and
            // packaged acceptance runs without ever touching a user's data.
            let data_dir = std::env::var_os("ECHOSPEAK_DESKTOP_DATA_DIR")
                .filter(|value| !value.is_empty())
                .map(std::path::PathBuf::from)
                .unwrap_or(app.path().app_local_data_dir()?.join("runtime"));
            let log_dir = std::env::var_os("ECHOSPEAK_DESKTOP_LOG_DIR")
                .filter(|value| !value.is_empty())
                .map(std::path::PathBuf::from)
                .unwrap_or(app.path().app_log_dir()?);
            let state = DesktopState::new(data_dir, log_dir).map_err(std::io::Error::other)?;
            app.manage(state.clone());
            if let Err(error) = backend::launch_backend(app.handle().clone(), state.clone()) {
                log::error!("Initial desktop backend launch failed: {error}");
                backend::recover_from_launch_failure(app.handle().clone(), state, error);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the EchoSpeak desktop application");

    app.run(|app_handle, event| {
        if matches!(
            event,
            tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
        ) {
            if let Some(state) = app_handle.try_state::<DesktopState>() {
                backend::shutdown_backend(state.inner());
            }
        }
    });
}
