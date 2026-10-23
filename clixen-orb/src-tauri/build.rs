// Every app command is listed here, so the webview can only call what a capability grants
// by name (`allow-<command>`). Adding a command without listing it here makes it uncallable.
const COMMANDS: &[&str] = &[
    "orb_status",
    "orb_send_text",
    "orb_send_audio",
    "orb_speak_reply",
    "orb_caption",
    "orb_prompt",
    "orb_prompt_done",
    "orb_cursor_fly",
    "orb_debug",
    "orb_cancel",
    "lifecycle_info",
    "lifecycle_accept",
    "lifecycle_advance",
    "lifecycle_lock",
    "lifecycle_unlock",
    "lifecycle_set_idle",
    "lifecycle_touch",
    "lifecycle_uninstall",
];

fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS))).expect("failed to run tauri-build");
}
