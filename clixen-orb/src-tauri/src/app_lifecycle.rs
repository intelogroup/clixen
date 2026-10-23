//! Tauri glue for the lifecycle: managed state, the `setup` window, commands, and the background
//! thread that supervises the sidecar and locks the vault when idle or after sleep.
//! Off by default (dev orb keeps using the launchd backend); on with `--features lifecycle` or
//! `CLIXEN_LIFECYCLE=1`.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::lifecycle::{LocalOllama, Lifecycle, Stage};
use crate::sidecar::{Sidecar, SidecarConfig, SidecarState};
use crate::vault::{VaultConfig, VaultManager};

pub fn enabled() -> bool {
    match std::env::var("CLIXEN_LIFECYCLE").as_deref() {
        Ok("1") => true,
        Ok("0") => false,
        _ => cfg!(feature = "lifecycle"),
    }
}

pub struct Shared {
    lc: Mutex<Lifecycle>,
    sidecar: Mutex<Option<Sidecar>>,
}

type State = Arc<Shared>;

#[derive(Serialize)]
pub struct Info {
    stage: Stage,
    idle_minutes: u64,
    mounted_secs: Option<u64>,
    sidecar: &'static str,
    vault_root: String,
}

fn data_dir(app: &AppHandle) -> PathBuf {
    std::env::var("CLIXEN_DATA_DIR").map(PathBuf::from).unwrap_or_else(|_| app.path().app_local_data_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

fn sidecar_config(vault_root: &std::path::Path) -> Option<SidecarConfig> {
    let cmd: Vec<String> = serde_json::from_str(&std::env::var("CLIXEN_SIDECAR_CMD").ok()?).ok()?;
    let port = std::env::var("CLIXEN_SIDECAR_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(9234);
    Some(SidecarConfig {
        cmd,
        env: vec![("CLIXEN_DATA_DIR".into(), vault_root.join("data").to_string_lossy().into_owned())],
        port,
        health_path: std::env::var("CLIXEN_SIDECAR_HEALTH").unwrap_or_else(|_| "/".into()),
        token: super::auth_token(),
        startup_grace: Duration::from_secs(45),
        max_restarts: 5,
        window: Duration::from_secs(120),
    })
}

fn emit(app: &AppHandle, stage: &Stage) {
    let _ = app.emit("lifecycle-stage", stage);
    let ready = *stage == Stage::Ready;
    if let Some(w) = app.get_webview_window("setup") {
        let _ = if ready { w.hide() } else { w.show().and_then(|_| w.set_focus()) };
    }
    if let Some(w) = app.get_webview_window("orb") {
        let _ = if ready { w.show() } else { w.hide() };
    }
}

fn info(sh: &Shared) -> Info {
    let lc = sh.lc.lock().unwrap();
    let sc = sh.sidecar.lock().unwrap();
    Info {
        stage: lc.stage().clone(),
        idle_minutes: lc.persisted.idle_minutes,
        mounted_secs: lc.mounted_secs(),
        sidecar: match sc.as_ref().map(|s| s.state()) {
            None => "external",
            Some(SidecarState::Stopped) => "stopped",
            Some(SidecarState::Starting) => "starting",
            Some(SidecarState::Healthy) => "healthy",
            Some(SidecarState::Failed) => "failed",
        },
        vault_root: lc.vault.root().to_string_lossy().into_owned(),
    }
}

/// Start the sidecar once the vault is mounted; stop it when the vault locks.
fn sync_sidecar(sh: &Shared) {
    let (ready, root) = {
        let lc = sh.lc.lock().unwrap();
        (*lc.stage() == Stage::Ready, lc.vault.root().to_path_buf())
    };
    let mut sc = sh.sidecar.lock().unwrap();
    if ready {
        if sc.is_none() {
            *sc = sidecar_config(&root).map(Sidecar::new);
        }
        if let Some(s) = sc.as_mut() {
            let _ = s.start();
        }
    } else if let Some(s) = sc.as_mut() {
        s.stop(); // the vault is about to detach: nothing may hold files open
    }
}

pub fn setup(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let dir = data_dir(app);
    let vault = VaultManager::new(VaultConfig {
        image: dir.join("vault").join("clixen.sparseimage"),
        mountpoint: dir.join("mnt"),
        service: std::env::var("CLIXEN_VAULT_SERVICE").unwrap_or_else(|_| "clixen.vault.app".into()), // override: tests must never touch the real entry
        account: "default".into(),
        size: "20g".into(),
    });
    let ollama_port = std::env::var("CLIXEN_OLLAMA_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(11434);
    let models = std::env::var("CLIXEN_MODELS").map(|m| m.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()).unwrap_or_else(|_| vec!["gemma4:12b".into(), "nomic-embed-text".into()]);
    let lc = Lifecycle::new(vault, Box::new(LocalOllama { port: ollama_port }), models, dir);
    let shared: State = Arc::new(Shared { lc: Mutex::new(lc), sidecar: Mutex::new(None) });
    app.manage(shared.clone());

    WebviewWindowBuilder::new(app, "setup", WebviewUrl::App("setup.html".into()))
        .title("Clixen")
        .inner_size(520.0, 600.0)
        .resizable(false)
        .center()
        .build()?;
    if let Some(w) = app.get_webview_window("orb") {
        let _ = w.hide();
    }

    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
    let h = app.clone();
    app.global_shortcut().on_shortcut(Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyS), move |_a, _s, e| {
        if e.state() == ShortcutState::Pressed {
            show_settings(&h);
        }
    })?;

    let app2 = app.clone();
    std::thread::spawn(move || {
        // first pass: resume wherever the stored state says (Onboard waits for the user)
        advance(&app2, &shared);
        let mut last_tick = Instant::now();
        let mut last_idle = Instant::now();
        loop {
            std::thread::sleep(Duration::from_secs(1));
            let now = Instant::now();
            // The loop sleeps 1s; a much longer gap means the Mac slept. Lock before anything else.
            if now.duration_since(last_tick) > Duration::from_secs(20) {
                let stage = {
                    let mut lc = shared.lc.lock().unwrap();
                    let _ = lc.on_suspend();
                    lc.stage().clone()
                };
                sync_sidecar(&shared);
                emit(&app2, &stage);
            }
            last_tick = now;
            if let Some(s) = shared.sidecar.lock().unwrap().as_mut() {
                s.tick();
            }
            if now.duration_since(last_idle) >= Duration::from_secs(15) {
                last_idle = now;
                let r = shared.lc.lock().unwrap().check_idle(now);
                match r {
                    Ok(true) => {
                        sync_sidecar(&shared);
                        emit(&app2, &Stage::Locked);
                    }
                    Err(e) => {
                        let _ = app2.emit("lifecycle-notice", e.to_string());
                    }
                    Ok(false) => {}
                }
            }
        }
    });
    Ok(())
}

fn advance(app: &AppHandle, sh: &State) -> Stage {
    let stage = sh.lc.lock().unwrap().advance(&mut |s| {
        let _ = app.emit("lifecycle-stage", s);
    });
    sync_sidecar(sh);
    emit(app, &stage);
    stage
}

/// Settings are only offered while the vault is unlocked; otherwise the stage screen shows.
pub fn show_settings(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("setup") {
        let _ = w.show().and_then(|_| w.set_focus());
        let _ = app.emit("lifecycle-settings", ());
    }
}

/// Closing the window while Ready just hides it; closing it mid-setup quits (nothing else is usable).
pub fn on_setup_close(app: &AppHandle, api: &tauri::CloseRequestApi) {
    let ready = app.try_state::<State>().is_some_and(|sh| *sh.lc.lock().unwrap().stage() == Stage::Ready);
    if ready {
        api.prevent_close();
        if let Some(w) = app.get_webview_window("setup") {
            let _ = w.hide();
        }
    } else {
        app.exit(0);
    }
}

/// Any orb use counts as activity for the idle lock.
pub fn touch(app: &AppHandle) {
    if let Some(sh) = app.try_state::<State>() {
        sh.lc.lock().unwrap().touch();
    }
}

/// Quit: stop the sidecar first so nothing holds a file open, then detach the vault.
pub fn on_exit(app: &AppHandle) {
    let Some(sh) = app.try_state::<State>() else { return };
    if let Some(s) = sh.sidecar.lock().unwrap().as_mut() {
        s.stop();
    }
    let r = sh.lc.lock().unwrap().on_quit();
    if let Err(e) = r {
        eprintln!("[orb] vault not locked on quit: {e}");
    }
}

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
    tauri::async_runtime::spawn_blocking(f).await.expect("lifecycle task panicked")
}

#[tauri::command]
pub async fn lifecycle_info(state: tauri::State<'_, State>) -> Result<Info, String> {
    let sh = state.inner().clone();
    Ok(blocking(move || info(&sh)).await)
}

#[tauri::command]
pub async fn lifecycle_accept(app: AppHandle, state: tauri::State<'_, State>) -> Result<Stage, String> {
    let sh = state.inner().clone();
    Ok(blocking(move || {
        sh.lc.lock().unwrap().accept_onboarding();
        advance(&app, &sh)
    })
    .await)
}

/// Retry from Ollama missing / Failed.
#[tauri::command]
pub async fn lifecycle_advance(app: AppHandle, state: tauri::State<'_, State>) -> Result<Stage, String> {
    let sh = state.inner().clone();
    Ok(blocking(move || {
        advance(&app, &sh)
    })
    .await)
}

#[tauri::command]
pub async fn lifecycle_lock(app: AppHandle, state: tauri::State<'_, State>) -> Result<Stage, String> {
    let sh = state.inner().clone();
    blocking(move || {
        if let Some(s) = sh.sidecar.lock().unwrap().as_mut() {
            s.stop();
        }
        let r = sh.lc.lock().unwrap().lock();
        let stage = sh.lc.lock().unwrap().stage().clone();
        sync_sidecar(&sh);
        emit(&app, &stage);
        r.map(|_| stage).map_err(|e| e.to_string())
    })
    .await
}

#[tauri::command]
pub async fn lifecycle_unlock(app: AppHandle, state: tauri::State<'_, State>) -> Result<Stage, String> {
    let sh = state.inner().clone();
    Ok(blocking(move || {
        let stage = sh.lc.lock().unwrap().unlock();
        sync_sidecar(&sh);
        emit(&app, &stage);
        stage
    })
    .await)
}

#[tauri::command]
pub async fn lifecycle_set_idle(minutes: u64, state: tauri::State<'_, State>) -> Result<u64, String> {
    let sh = state.inner().clone();
    Ok(blocking(move || {
        let mut lc = sh.lc.lock().unwrap();
        lc.set_idle_minutes(minutes);
        lc.persisted.idle_minutes
    })
    .await)
}

#[tauri::command]
pub async fn lifecycle_touch(state: tauri::State<'_, State>) -> Result<(), String> {
    state.lc.lock().unwrap().touch();
    Ok(())
}

#[tauri::command]
pub async fn lifecycle_uninstall(app: AppHandle, confirm: String, state: tauri::State<'_, State>) -> Result<Stage, String> {
    let sh = state.inner().clone();
    blocking(move || {
        if let Some(s) = sh.sidecar.lock().unwrap().as_mut() {
            s.stop();
        }
        let r = sh.lc.lock().unwrap().uninstall(&confirm);
        let stage = sh.lc.lock().unwrap().stage().clone();
        emit(&app, &stage);
        r.map(|_| stage)
    })
    .await
}
