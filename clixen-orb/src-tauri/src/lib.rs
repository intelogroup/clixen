//! Clixen Orb — a UI-less floating voice orb for macOS.
//!
//! The orb is an `NSPanel` (via tauri-nspanel) so it can float above every other
//! window — browser tabs, editors, fullscreen apps — without ever stealing focus.
//! Network + Keychain access live on the Rust side; the webview only renders.
//!
//! Two windows:
//!   `orb`     150x150 — the sphere itself. Deliberately tight so its transparent
//!            corners can never intercept a click meant for the page underneath.
//!   `caption` 420x78  — the whisper strip. Ignores mouse events entirely.

pub mod app_lifecycle;
pub mod lifecycle;
pub mod sidecar;
pub mod vault;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::sync::OnceLock;

use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine as _;
use futures_util::StreamExt;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, WebviewWindow};
use tauri_nspanel::{
    tauri_panel, CollectionBehavior, ManagerExt, PanelLevel, WebviewWindowExt,
};

// ---------------------------------------------------------------------------
// Panel class
// ---------------------------------------------------------------------------

tauri_panel! {
    panel!(OrbPanel {
        config: {
            can_become_key_window: true,
            can_become_main_window: false,
            is_floating_panel: true,
            works_when_modal: true
        }
    })
}

// ---------------------------------------------------------------------------
// Backend (Clixen FastAPI harness) config + auth
// ---------------------------------------------------------------------------

fn backend_url() -> String {
    std::env::var("CLIXEN_BACKEND_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:9234".to_string())
        .trim_end_matches('/')
        .to_string()
}

/// Per-install localhost token, stored in the macOS Keychain by
/// `tools-harness/tools/auth_token.py` under service `clixen.localhost.token`.
fn auth_token() -> Option<String> {
    static TOKEN: OnceLock<Option<String>> = OnceLock::new();
    TOKEN
        .get_or_init(|| {
            let out = std::process::Command::new("security")
                .args(["find-generic-password", "-s", "clixen.localhost.token", "-w"])
                .output()
                .ok()?;
            if !out.status.success() {
                return None;
            }
            let raw = String::from_utf8_lossy(&out.stdout).trim().to_string();
            match serde_json::from_str::<serde_json::Value>(&raw) {
                Ok(v) => v
                    .get("value")
                    .and_then(|t| t.as_str())
                    .map(String::from),
                Err(_) => Some(raw),
            }
        })
        .clone()
}

fn http() -> reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT
        .get_or_init(|| {
            reqwest::Client::builder()
                .no_proxy()
                .build()
                .expect("failed to build http client")
        })
        .clone()
}

fn get(url: String) -> reqwest::RequestBuilder {
    let mut r = http().get(url);
    if let Some(t) = auth_token() {
        r = r.header("x-clixen-token", t);
    }
    r
}

fn post(url: String) -> reqwest::RequestBuilder {
    let mut r = http().post(url);
    if let Some(t) = auth_token() {
        r = r.header("x-clixen-token", t);
    }
    r
}

// ---------------------------------------------------------------------------
// SSE helper
// ---------------------------------------------------------------------------

/// Set `CLIXEN_ORB_DEBUG=1` to trace every SSE frame the backend sends.
/// Without it the orb is silent; with it, a dropped or misnamed frame shows up
/// in the log instead of as an orb that simply never answers.
fn debug_enabled() -> bool {
    static ON: OnceLock<bool> = OnceLock::new();
    *ON.get_or_init(|| std::env::var("CLIXEN_ORB_DEBUG").is_ok())
}

#[derive(Clone, Serialize)]
struct SseEvent {
    /// One of: token | transcript | audio | done | error
    ///
    /// Serialised as `kind`, not `type` — the renderer switches on
    /// `payload.kind`, and a rename here silently drops every frame.
    kind: String,
    text: String,
    data: Option<String>,
}

/// Split an SSE byte stream into events and relay each to the `orb` window.
async fn pump_sse(app: &AppHandle, resp: reqwest::Response) {
    let mut body = resp.bytes_stream();
    let mut buf = String::new();

    while let Some(chunk) = body.next().await {
        let Ok(chunk) = chunk else { break };
        buf.push_str(&String::from_utf8_lossy(&chunk));

        while let Some(idx) = buf.find("\n\n") {
            let frame: String = buf.drain(..idx + 2).collect();
            for line in frame.lines() {
                let data = match line.strip_prefix("data: ").or_else(|| line.strip_prefix("data:")) {
                    Some(d) => d.trim(),
                    None => continue,
                };
                let Ok(v) = serde_json::from_str::<serde_json::Value>(data) else {
                    continue;
                };

                // `/voice/process` tags frames with `type`; `/chat/stream` does not.
                let kind = v
                    .get("type")
                    .and_then(|t| t.as_str())
                    .map(String::from)
                    .unwrap_or_else(|| {
                        if v.get("done").and_then(|d| d.as_bool()).unwrap_or(false) {
                            "done".to_string()
                        } else {
                            "token".to_string()
                        }
                    });

                let text = v
                    .get("text")
                    .and_then(|t| t.as_str())
                    .or_else(|| v.get("token").and_then(|t| t.as_str()))
                    .unwrap_or("")
                    .to_string();

                let ev = SseEvent {
                    kind,
                    text,
                    data: v.get("data").and_then(|d| d.as_str()).map(String::from),
                };

                if debug_enabled() {
                    let preview: String = ev.text.chars().take(60).collect();
                    eprintln!(
                        "[orb] sse kind={} text={preview:?} audio={}",
                        ev.kind,
                        ev.data.is_some()
                    );
                }

                // Only the orb window consumes `orb-event`.
                if let Some(w) = app.get_webview_window("orb") {
                    let _ = w.emit("orb-event", &ev);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Persistent state (where the user dragged the orb to)
// ---------------------------------------------------------------------------

fn state_path(app: &AppHandle) -> PathBuf {
    let dir = app
        .path()
        .app_config_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    let _ = std::fs::create_dir_all(&dir);
    dir.join("orb-state.json")
}

#[derive(Default, serde::Deserialize, serde::Serialize)]
struct OrbState {
    x: Option<f64>,
    y: Option<f64>,
}

fn load_state(app: &AppHandle) -> OrbState {
    std::fs::read_to_string(state_path(app))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_state(app: &AppHandle, state: &OrbState) {
    if let Ok(s) = serde_json::to_string(state) {
        let _ = std::fs::write(state_path(app), s);
    }
}

/// Keep the orb on a visible screen.
///
/// A restored position can be stale (an old display was unplugged) or simply
/// wrong, and an orb parked off-screen is an orb the user can never get back.
/// If the saved point isn't on any monitor, drop it back to the middle of the
/// first one. Returns logical coordinates.
fn clamp_to_visible(window: &WebviewWindow, x: f64, y: f64) -> (f64, f64) {
    const ORB: f64 = 150.0;

    let Ok(monitors) = window.available_monitors() else {
        return (x, y);
    };

    let mut fallback = None;
    for m in &monitors {
        let mscale = m.scale_factor().max(0.01);
        let mx = m.position().x as f64 / mscale;
        let my = m.position().y as f64 / mscale;
        let mw = m.size().width as f64 / mscale;
        let mh = m.size().height as f64 / mscale;

        // Still on this monitor if any part of the orb would land on it.
        if x + ORB > mx && x < mx + mw && y + ORB > my && y < my + mh {
            return (x, y);
        }
        if fallback.is_none() {
            fallback = Some((mx, my, mw, mh));
        }
    }

    match fallback {
        Some((mx, my, mw, mh)) => (
            (mx + (mw - ORB) / 2.0).max(mx),
            (my + (mh - ORB) / 2.0).max(my),
        ),
        None => (x, y),
    }
}

// ---------------------------------------------------------------------------
// Caption window (the whisper strip)
// ---------------------------------------------------------------------------

const CAPTION_GAP: i32 = 6;

/// Slide the caption window into place directly beneath the orb.
fn position_caption(app: &AppHandle) {
    let (Some(orb), Some(cap)) = (app.get_webview_window("orb"), app.get_webview_window("caption"))
    else {
        return;
    };
    let (Ok(opos), Ok(osize), Ok(csize)) = (orb.outer_position(), orb.outer_size(), cap.outer_size())
    else {
        return;
    };
    let x = opos.x + (osize.width as i32 - csize.width as i32) / 2;
    let y = opos.y + osize.height as i32 + CAPTION_GAP;
    let _ = cap.set_position(tauri::Position::Physical(tauri::PhysicalPosition::new(x, y)));
}

#[tauri::command]
fn orb_caption(app: AppHandle, text: String, kind: Option<String>, persist: Option<bool>) {
    let Some(cap) = app.get_webview_window("caption") else { return };

    if text.trim().is_empty() {
        let _ = cap.emit(
            "orb-caption",
            serde_json::json!({ "text": "", "kind": "", "persist": false }),
        );
        let _ = cap.hide();
        return;
    }

    position_caption(&app);
    let _ = cap.set_ignore_cursor_events(true);
    let _ = cap.show();
    let _ = cap.emit(
        "orb-caption",
        serde_json::json!({
            "text": text,
            "kind": kind.unwrap_or_default(),
            "persist": persist.unwrap_or(false),
        }),
    );
}

/// The app that had focus before the typed prompt took it. Remembered so we
/// can hand the keyboard back when the prompt is dismissed.
static PREVIOUS_APP: Mutex<Option<i32>> = Mutex::new(None);

/// Open the typed prompt.
///
/// The orb is a non-activating panel, so its webview can never become first
/// responder and `prompt()` inside it returns nothing. The caption strip is an
/// ordinary window: Rust focuses it and takes activation for the duration of
/// the prompt, then `orb_prompt_done` gives the focus back.
#[tauri::command]
fn orb_prompt(app: AppHandle) {
    use tauri_nspanel::objc2_app_kit::{NSApplication, NSWorkspace};

    let Some(cap) = app.get_webview_window("caption") else { return };

    let handle = app.clone();
    app.run_on_main_thread(move || {
        // Remember who had focus so we can return it.
        let ws = NSWorkspace::sharedWorkspace();
        if let Some(front) = ws.frontmostApplication() {
            let pid = front.processIdentifier();
            if pid != std::process::id() as i32 {
                if let Ok(mut g) = PREVIOUS_APP.lock() {
                    *g = Some(pid);
                }
            }
        }
        // Take activation just long enough for the keystrokes to land.
        if let Some(mtm) = tauri_nspanel::objc2::MainThreadMarker::new() {
            let nsapp = NSApplication::sharedApplication(mtm);
            nsapp.activate();
        }

        position_caption(&handle);
        // The strip has to be clickable now, so it can't ignore the mouse.
        let _ = cap.set_ignore_cursor_events(false);
        let _ = cap.show();
        let _ = cap.emit(
            "orb-caption",
            serde_json::json!({ "text": "", "kind": "", "persist": false, "input": true }),
        );
        let _ = cap.set_focus();
    })
    .ok();
}

/// Dismiss the typed prompt and give the focus back to whoever had it.
#[tauri::command]
fn orb_prompt_done(app: AppHandle) {
    use tauri_nspanel::objc2_app_kit::{NSApplicationActivationOptions, NSRunningApplication};

    if let Some(cap) = app.get_webview_window("caption") {
        let _ = cap.set_ignore_cursor_events(true);
        let _ = cap.hide();
    }

    let prev = PREVIOUS_APP.lock().ok().and_then(|mut g| g.take());
    if let Some(pid) = prev {
        app.run_on_main_thread(move || {
            if let Some(running) = NSRunningApplication::runningApplicationWithProcessIdentifier(pid)
            {
                running.activateWithOptions(NSApplicationActivationOptions::ActivateAllWindows);
            }
        })
        .ok();
    }
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

#[derive(Serialize)]
struct OrbStatus {
    backend: String,
    token_present: bool,
    reachable: bool,
    error: Option<String>,
}

#[tauri::command]
async fn orb_status() -> OrbStatus {
    let backend = backend_url();
    let token_present = auth_token().is_some();
    let url = format!("{backend}/api/auth/session");
    match get(url).timeout(std::time::Duration::from_secs(3)).send().await {
        Ok(r) if r.status().is_success() => OrbStatus {
            backend,
            token_present,
            reachable: true,
            error: None,
        },
        Ok(r) => OrbStatus {
            backend,
            token_present,
            reachable: false,
            error: Some(format!("HTTP {}", r.status())),
        },
        Err(e) => OrbStatus {
            backend,
            token_present,
            reachable: false,
            error: Some(e.to_string()),
        },
    }
}

/// Stream a typed message through `/chat/stream`.
#[tauri::command]
async fn orb_send_text(app: AppHandle, message: String, chat_id: Option<String>) {
    app_lifecycle::touch(&app);
    let backend = backend_url();
    let chat_id = chat_id.unwrap_or_else(|| "orb_ui".to_string());
    let url = format!(
        "{backend}/chat/stream?message={}&chat_id={}",
        urlencode(&message),
        urlencode(&chat_id)
    );

    match get(url)
        .timeout(std::time::Duration::from_secs(320))
        .send()
        .await
    {
        Ok(resp) if resp.status().is_success() => pump_sse(&app, resp).await,
        Ok(resp) => emit_error(&app, format!("backend HTTP {}", resp.status())),
        Err(e) => emit_error(&app, e.to_string()),
    }
}

/// Push a recorded clip through `/voice/process`:
/// transcribe → orchestrator → optional sentence-by-sentence TTS.
#[tauri::command]
async fn orb_send_audio(
    app: AppHandle,
    audio_b64: String,
    chat_id: Option<String>,
    tts: Option<bool>,
) {
    app_lifecycle::touch(&app);
    let backend = backend_url();
    let chat_id = chat_id.unwrap_or_else(|| "orb_ui".to_string());
    let Ok(bytes) = B64.decode(audio_b64) else {
        emit_error(&app, "bad audio payload".into());
        return;
    };

    let part = reqwest::multipart::Part::bytes(bytes)
        .file_name("orb.webm")
        .mime_str("audio/webm")
        .unwrap_or_else(|_| reqwest::multipart::Part::bytes(Vec::new()));

    let form = reqwest::multipart::Form::new()
        .part("file", part)
        .text("chat_id", chat_id)
        .text("tts", if tts.unwrap_or(false) { "1" } else { "0" })
        .text("web_search", "0");

    match post(format!("{backend}/voice/process"))
        .multipart(form)
        .timeout(std::time::Duration::from_secs(320))
        .send()
        .await
    {
        Ok(resp) if resp.status().is_success() => pump_sse(&app, resp).await,
        Ok(resp) => emit_error(&app, format!("backend HTTP {}", resp.status())),
        Err(e) => emit_error(&app, e.to_string()),
    }
}

/// Typed input gets no audio back from `/chat/stream`, so a reply that was
/// typed rather than spoken has to be voiced separately. `POST /tts/speak/stream`
/// splits the reply into sentences and emits one `{"type":"audio","data":<b64>}`
/// frame per synthesized clip, then `{"type":"done"}` — the renderer just plays
/// them as they land.
#[tauri::command]
async fn orb_speak_reply(app: AppHandle, text: String, voice: Option<String>) {
    let backend = backend_url();

    // Only send `voice` when we actually have one: the backend defaults to
    // "af_heart", but an explicit empty string overrides that default and
    // makes the synthesizer fail outright.
    let mut body = serde_json::json!({ "text": text });
    if let Some(v) = voice.filter(|v| !v.trim().is_empty()) {
        body["voice"] = serde_json::Value::String(v);
    }

    let resp = match post(format!("{backend}/tts/speak/stream"))
        .json(&body)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => {
            emit_error(&app, format!("tts HTTP {}", r.status()));
            return;
        }
        Err(e) => {
            emit_error(&app, e.to_string());
            return;
        }
    };

    pump_sse(&app, resp).await;
}

fn emit_error(app: &AppHandle, msg: String) {
    if let Some(w) = app.get_webview_window("orb") {
        let _ = w.emit(
            "orb-event",
            SseEvent { kind: "error".into(), text: msg, data: None },
        );
    }
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

fn urlencode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Cursor overlay — the orb's cursor flies to wherever an action clicked
// ---------------------------------------------------------------------------

/// Backend tools (`tools-harness/tools/orb_cursor.py`) drop the click target here.
fn cursor_file() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config/g4l/data/orb_cursor.json")
}

/// Index of the monitor rect (logical x, y, w, h) that holds the point, if any.
fn pick_monitor(rects: &[(f64, f64, f64, f64)], x: f64, y: f64) -> Option<usize> {
    rects.iter().position(|&(mx, my, mw, mh)| x >= mx && x < mx + mw && y >= my && y < my + mh)
}

/// Move the overlay onto the monitor holding logical point (x, y); returns that monitor's
/// logical origin, or None when no monitor holds it (the overlay stays where it is).
fn follow_target(cur: &WebviewWindow, x: f64, y: f64) -> Option<(f64, f64)> {
    let monitors = cur.available_monitors().ok()?;
    let rects: Vec<_> = monitors
        .iter()
        .map(|m| {
            let s = m.scale_factor().max(0.01);
            (m.position().x as f64 / s, m.position().y as f64 / s, m.size().width as f64 / s, m.size().height as f64 / s)
        })
        .collect();
    let i = pick_monitor(&rects, x, y)?;
    let (mx, my, _, _) = rects[i];
    let m = &monitors[i];
    let _ = cur.set_size(tauri::Size::Physical(*m.size()));
    let _ = cur.set_position(tauri::Position::Physical(*m.position()));
    Some((mx, my))
}

/// Send the overlay cursor from the orb's centre to a screen point (logical px,
/// top-left origin). The overlay window covers the monitor, so subtract its origin.
fn cursor_fly(app: &AppHandle, x: f64, y: f64, click: bool, style: Option<String>, travel: Option<String>) {
    let (Some(orb), Some(cur)) = (app.get_webview_window("orb"), app.get_webview_window("cursor"))
    else {
        return;
    };
    let scale = orb.scale_factor().unwrap_or(1.0).max(0.01);
    let (Ok(op), Ok(os)) = (orb.outer_position(), orb.outer_size()) else { return };
    // The overlay covers one monitor at a time: follow the target onto whichever one holds it.
    let off = follow_target(&cur, x, y).unwrap_or_else(|| {
        cur.outer_position()
            .map(|p| (p.x as f64 / scale, p.y as f64 / scale))
            .unwrap_or((0.0, 0.0))
    });
    let from = (
        (op.x as f64 + os.width as f64 / 2.0) / scale - off.0,
        (op.y as f64 + os.height as f64 / 2.0) / scale - off.1,
    );
    // App-wide: the overlay animates it, the orb hides its own in-canvas cursor.
    let _ = app.emit(
        "cursor-fly",
        serde_json::json!({ "from": [from.0, from.1], "to": [x - off.0, y - off.1], "click": click, "style": style, "travel": travel }),
    );
}

/// A click target from the frontend or another process.
#[tauri::command]
fn orb_cursor_fly(app: AppHandle, x: f64, y: f64, click: Option<bool>) {
    cursor_fly(&app, x, y, click.unwrap_or(true), None, None);
}

/// Poll the backend's target file. Entries older than 3s are ignored, and the
/// sequence seen at startup is skipped, so a stale file never replays a click.
fn spawn_cursor_watcher(app: AppHandle) {
    std::thread::spawn(move || {
        let read = || {
            std::fs::read_to_string(cursor_file())
                .ok()
                .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        };
        let mut last_seq = read().and_then(|v| v.get("seq").and_then(|s| s.as_u64())).unwrap_or(0);
        loop {
            std::thread::sleep(std::time::Duration::from_millis(80));
            let Some(v) = read() else { continue };
            let seq = v.get("seq").and_then(|s| s.as_u64()).unwrap_or(0);
            if seq == last_seq {
                continue;
            }
            last_seq = seq;
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            if now - v.get("ts").and_then(|t| t.as_f64()).unwrap_or(0.0) > 3.0 {
                continue;
            }
            // Typing: the overlay shows a hand + text bubble at the parked cursor.
            if let Some(t) = v.get("type") {
                let _ = app.emit("cursor-type", t.clone());
                continue;
            }
            // Scrolling: the overlay shows a two-finger swipe at the parked cursor.
            if let Some(t) = v.get("scroll") {
                let _ = app.emit("cursor-scroll", t.clone());
                continue;
            }
            if let (Some(x), Some(y)) = (
                v.get("x").and_then(|x| x.as_f64()),
                v.get("y").and_then(|y| y.as_f64()),
            ) {
                cursor_fly(
                    &app,
                    x,
                    y,
                    v.get("click").and_then(|c| c.as_bool()).unwrap_or(true),
                    v.get("style").and_then(|c| c.as_str()).map(String::from),
                    v.get("travel").and_then(|c| c.as_str()).map(String::from),
                );
            }
        }
    });
}

/// Size the overlay to the orb's monitor and make it click-through.
fn setup_cursor_overlay(app: &AppHandle, orb: &WebviewWindow) {
    let Some(cur) = app.get_webview_window("cursor") else { return };
    if let Ok(Some(m)) = orb.current_monitor() {
        let _ = cur.set_size(tauri::Size::Physical(*m.size()));
        let _ = cur.set_position(tauri::Position::Physical(*m.position()));
    }
    let _ = cur.set_ignore_cursor_events(true);
}

// ---------------------------------------------------------------------------
// setup
// ---------------------------------------------------------------------------

static PASS_THROUGH: AtomicBool = AtomicBool::new(false);
static PINNED: AtomicBool = AtomicBool::new(true);
static LAST_MOVE: Mutex<Option<(i32, i32)>> = Mutex::new(None);

fn setup_orb_window(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let window: WebviewWindow = app.get_webview_window("orb").ok_or("orb window missing")?;

    // Restore where the user dragged it to last time.
    let st = load_state(app);
    if let (Some(x), Some(y)) = (st.x, st.y) {
        let (x, y) = clamp_to_visible(&window, x, y);
        let _ = window.set_position(tauri::Position::Logical(tauri::LogicalPosition::new(x, y)));
    }

    // ---- make the window a non-activating panel -----------------------------
    //
    // Order matters enormously here.
    //
    // `NSNonactivatingPanelMask` has to be on the style mask BEFORE the window's
    // class is swapped from NSWindow to our NSPanel subclass. `setStyleMask:`
    // makes AppKit tear down and rebuild the window, and during teardown
    // WebKit's `WKWindowVisibilityObserver` removes its KVO registration for
    // the key path "contentLayoutRect". If the class was already swapped, that
    // removal fails with
    //
    //     Cannot remove an observer <WKWindowVisibilityObserver ...> for the key
    //     path "contentLayoutRect" from <OrbPanel ...> because it is not
    //     registered as an observer.
    //
    // The ObjC exception aborts `setStyleMask:` halfway through, leaving the
    // window with a nil contentView. The WKWebView is then orphaned: the canvas
    // stays blank, `document.visibilityState` reports "hidden", and
    // `requestAnimationFrame` never fires — all with no error surfaced
    // anywhere. Setting the bit on the still-plain NSWindow avoids all of it.
    {
        use tauri_nspanel::objc2::msg_send;
        use tauri_nspanel::objc2::runtime::AnyObject;
        let nsw = window.ns_window()? as *mut AnyObject;
        unsafe {
            let mask: usize = msg_send![nsw, styleMask];
            let nonactivating = tauri_nspanel::objc2_app_kit::NSWindowStyleMask::NonactivatingPanel.0 as usize;
            if mask & nonactivating == 0 {
                let new_mask = mask | nonactivating;
                let r = tauri_nspanel::objc2::exception::catch(std::panic::AssertUnwindSafe(|| {
                    let _: () = msg_send![nsw, setStyleMask: new_mask];
                }));
                match r {
                    Ok(()) => eprintln!("[orb] non-activating mask {mask:#x} -> {new_mask:#x}"),
                    Err(Some(e)) => eprintln!("[orb] setStyleMask rejected: {e}"),
                    Err(None) => eprintln!("[orb] setStyleMask raised an unknown ObjC exception"),
                }
            }
        }
    }

    // `to_panel` (not `from_window`): only `to_panel` registers the panel in
    // the app's panel manager. Without that registration every later
    // `get_webview_panel("orb")` returns PanelNotFound, and the shortcuts that
    // reach for the panel do nothing at all.
    let panel = window.to_panel::<OrbPanel>()?;

    // The class swap skips NSPanel's initialiser, so WindowServer never learned
    // that this window must not steal activation. Sync the tag by hand.
    {
        use tauri_nspanel::objc2::msg_send;
        use tauri_nspanel::objc2::runtime::AnyObject;
        use tauri_nspanel::objc2::sel;
        let nsw = window.ns_window()? as *mut AnyObject;
        unsafe {
            let selector = sel!(_setPreventsActivation:);
            let responds: bool = msg_send![nsw, respondsToSelector: selector];
            if responds {
                let _: () = msg_send![nsw, _setPreventsActivation: true];
            }
        }
    }

    panel.set_floating_panel(true);
    // Status level + full-screen-auxiliary is what lets the orb ride above a
    // browser tab, a terminal, and a full-screen video alike.
    panel.set_level(PanelLevel::Status.value());
    panel.set_has_shadow(false);
    panel.set_opaque(false);
    panel.set_hides_on_deactivate(false);
    panel.set_collection_behavior(
        CollectionBehavior::new()
            .can_join_all_spaces()
            .ignores_cycle()
            .full_screen_auxiliary()
            .value(),
    );
    let _ = panel.show();

    setup_cursor_overlay(app, &window);
    spawn_cursor_watcher(app.clone());

    // The caption is pure decoration — never let it eat a click.
    if let Some(cap) = app.get_webview_window("caption") {
        let _ = cap.set_ignore_cursor_events(true);
        let _ = cap.hide();
    }

    // ---- global shortcuts -------------------------------------------------
    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

    // The Panel trait exposes no level getter, so mirror it ourselves.
    PINNED.store(true, Ordering::Relaxed);

    // ⌃⇧Space — summon / dismiss the orb.
    app.global_shortcut().on_shortcut(
        Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space),
        |_app, _shortcut, event| {
            if event.state() != ShortcutState::Pressed {
                return;
            }
            if debug_enabled() {
                eprintln!("[orb] shortcut ctrl+shift+space");
            }
            if let Ok(panel) = _app.get_webview_panel("orb") {
                if panel.is_visible() {
                    panel.hide();
                    if let Some(cap) = _app.get_webview_window("caption") {
                        let _ = cap.hide();
                    }
                    // Dismissing the orb must also drop the microphone.
                    if let Some(w) = _app.get_webview_window("orb") {
                        let _ = w.emit("orb-stop-listening", ());
                    }
                } else {
                    panel.show();
                }
            }
        },
    )?;

    // ⌃⇧P — let clicks fall through to whatever page the orb is sitting on.
    app.global_shortcut().on_shortcut(
        Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyP),
        |_app, _shortcut, event| {
            if event.state() != ShortcutState::Pressed {
                return;
            }
            if debug_enabled() {
                eprintln!("[orb] shortcut ctrl+shift+p");
            }
            let next = !PASS_THROUGH.load(Ordering::Relaxed);
            PASS_THROUGH.store(next, Ordering::Relaxed);
            if let Ok(panel) = _app.get_webview_panel("orb") {
                panel.set_ignores_mouse_events(next);
            }
        },
    )?;

    // ⌃⇧O — ride above full-screen apps (Status) vs. just above windows (Floating).
    app.global_shortcut().on_shortcut(
        Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyO),
        |_app, _shortcut, event| {
            if event.state() != ShortcutState::Pressed {
                return;
            }
            if debug_enabled() {
                eprintln!("[orb] shortcut ctrl+shift+o");
            }
            let pinned = !PINNED.load(Ordering::Relaxed);
            PINNED.store(pinned, Ordering::Relaxed);
            if let Ok(panel) = _app.get_webview_panel("orb") {
                panel.set_level(if pinned {
                    PanelLevel::Status.value()
                } else {
                    PanelLevel::Floating.value()
                });
            }
        },
    )?;

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_nspanel::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            let handle = app.handle().clone();
            if let Err(e) = setup_orb_window(&handle) {
                eprintln!("[orb] setup failed: {e}");
            }
            if app_lifecycle::enabled() {
                app_lifecycle::setup(&handle)?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // A hidden orb is a dismissed orb — make sure the mic isn't left
            // running behind a panel the user can no longer see.
            if let tauri::WindowEvent::Destroyed = event {
                let _ = window.emit("orb-stop-listening", ());
            }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "setup" {
                    app_lifecycle::on_setup_close(window.app_handle(), api);
                }
            }
            if window.label() != "orb" {
                return;
            }
            // Keep the whisper strip pinned beneath the orb while it's dragged,
            // and remember where the user parked it.
            if let tauri::WindowEvent::Moved(pos) = event {
                // tao reports the move in PHYSICAL pixels, but we persist
                // LOGICAL ones (that's what `set_position(Logical)` expects).
                // Mixing the two doubles the orb's offset on every launch and
                // eventually strands it off-screen, so convert once, here.
                let scale = window.scale_factor().unwrap_or(1.0).max(0.01);
                let p = (
                    (pos.x as f64 / scale).round() as i32,
                    (pos.y as f64 / scale).round() as i32,
                );
                let changed = match LAST_MOVE.lock() {
                    Ok(mut g) => {
                        let changed = *g != Some(p);
                        if changed {
                            *g = Some(p);
                        }
                        changed
                    }
                    Err(_) => true,
                };
                if changed {
                    position_caption(window.app_handle());
                    let app = window.app_handle().clone();
                    std::thread::spawn(move || {
                        let mut st = load_state(&app);
                        st.x = Some(p.0 as f64);
                        st.y = Some(p.1 as f64);
                        save_state(&app, &st);
                    });
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            orb_status,
            orb_send_text,
            orb_send_audio,
            orb_speak_reply,
            orb_caption,
            orb_prompt,
            orb_prompt_done,
            orb_cursor_fly,
            app_lifecycle::lifecycle_info,
            app_lifecycle::lifecycle_accept,
            app_lifecycle::lifecycle_advance,
            app_lifecycle::lifecycle_lock,
            app_lifecycle::lifecycle_unlock,
            app_lifecycle::lifecycle_set_idle,
            app_lifecycle::lifecycle_touch,
            app_lifecycle::lifecycle_uninstall,
        ])
        .build(tauri::generate_context!())
        .expect("error while building clixen orb")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                app_lifecycle::on_exit(app);
            }
        });
}
#[cfg(test)]
mod tests {
    use super::pick_monitor;

    #[test]
    fn picks_the_monitor_holding_the_point() {
        let rects = [(0.0, 0.0, 1512.0, 982.0), (1512.0, -200.0, 1920.0, 1080.0), (-1280.0, 0.0, 1280.0, 800.0)];
        assert_eq!(pick_monitor(&rects, 100.0, 100.0), Some(0));
        assert_eq!(pick_monitor(&rects, 1600.0, -50.0), Some(1));
        assert_eq!(pick_monitor(&rects, -10.0, 10.0), Some(2));
        assert_eq!(pick_monitor(&rects, 1512.0, 0.0), Some(1));
        assert_eq!(pick_monitor(&rects, 5000.0, 0.0), None);
    }
}
