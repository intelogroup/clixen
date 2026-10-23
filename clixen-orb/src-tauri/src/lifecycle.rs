//! App lifecycle: first launch (Ollama -> models -> vault -> workspace), relaunch (mount), idle /
//! suspend / quit (unmount), locked -> unlock, and uninstall. A plain state machine over a
//! `VaultManager` and an `Ollama` probe, so every transition is testable without a UI.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::vault::{VaultError, VaultManager, VaultStatus};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "stage", content = "detail", rename_all = "snake_case")]
pub enum Stage {
    /// Waiting on the user to accept the privacy explainer.
    Onboard,
    /// Ollama is not running or not installed.
    OllamaMissing,
    /// Pulling a model: name and percent.
    Models(String, u8),
    VaultCreate,
    Ready,
    /// Vault unmounted (idle, suspend, or the user locked it). `unlock` remounts.
    Locked,
    /// A step failed; message is shown verbatim and `advance` retries.
    Failed(String),
}

pub trait Ollama {
    fn up(&self) -> bool;
    fn installed(&self) -> Vec<String>;
    /// Pull one model, reporting percent as it goes.
    fn pull(&self, model: &str, progress: &mut dyn FnMut(u8)) -> Result<(), String>;
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Persisted {
    pub onboarded: bool,
    pub idle_minutes: u64,
}

impl Default for Persisted {
    fn default() -> Self {
        Self { onboarded: false, idle_minutes: 15 }
    }
}

pub struct Lifecycle {
    pub vault: VaultManager,
    ollama: Box<dyn Ollama + Send>,
    pub models: Vec<String>,
    /// App data dir: holds `lifecycle.json`; the vault image lives inside it too.
    pub data_dir: PathBuf,
    pub persisted: Persisted,
    last_activity: Instant,
    stage: Stage,
}

impl Lifecycle {
    pub fn new(vault: VaultManager, ollama: Box<dyn Ollama + Send>, models: Vec<String>, data_dir: PathBuf) -> Self {
        let persisted: Persisted = std::fs::read_to_string(data_dir.join("lifecycle.json")).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default();
        let stage = if persisted.onboarded { Stage::VaultCreate } else { Stage::Onboard };
        Self { vault, ollama, models, data_dir, persisted, last_activity: Instant::now(), stage }
    }

    pub fn stage(&self) -> &Stage {
        &self.stage
    }

    fn save(&self) {
        let _ = std::fs::create_dir_all(&self.data_dir);
        if let Ok(t) = serde_json::to_string_pretty(&self.persisted) {
            let _ = std::fs::write(self.data_dir.join("lifecycle.json"), t);
        }
    }

    pub fn accept_onboarding(&mut self) {
        self.persisted.onboarded = true;
        self.save();
        if self.stage == Stage::Onboard {
            self.stage = Stage::OllamaMissing; // re-checked by the next advance
        }
    }

    pub fn set_idle_minutes(&mut self, m: u64) {
        self.persisted.idle_minutes = m.clamp(1, 240);
        self.save();
    }

    pub fn touch(&mut self) {
        self.last_activity = Instant::now();
    }

    fn missing_models(&self) -> Vec<String> {
        let have = self.ollama.installed();
        self.models.iter().filter(|m| !have.iter().any(|h| h == *m || h.strip_suffix(":latest") == Some(m.as_str()))).cloned().collect()
    }

    fn init_workspace(&self) -> std::io::Result<()> {
        for d in ["workspace", "data"] {
            std::fs::create_dir_all(self.vault.root().join(d))?;
        }
        std::fs::write(self.vault.root().join(".clixen-vault"), "1")
    }

    /// Run steps until the app is Ready or a step needs the user (Onboard, OllamaMissing, Failed).
    /// `on_stage` fires on every change so the UI can follow along.
    pub fn advance(&mut self, on_stage: &mut dyn FnMut(&Stage)) -> Stage {
        let set = |me: &mut Self, s: Stage, cb: &mut dyn FnMut(&Stage)| {
            me.stage = s;
            cb(&me.stage);
        };
        if matches!(self.stage, Stage::Onboard | Stage::Ready | Stage::Locked) {
            return self.stage.clone();
        }
        // Ollama
        if !self.ollama.up() {
            set(self, Stage::OllamaMissing, on_stage);
            return self.stage.clone();
        }
        // Models
        for m in self.missing_models() {
            set(self, Stage::Models(m.clone(), 0), on_stage);
            let mut last = 0u8;
            let mut report = |p: u8| last = p;
            if let Err(e) = self.ollama.pull(&m, &mut report) {
                set(self, Stage::Failed(format!("could not pull {m}: {e}")), on_stage);
                return self.stage.clone();
            }
            set(self, Stage::Models(m, 100), on_stage);
        }
        // Vault
        if self.vault.status() == VaultStatus::NotCreated {
            set(self, Stage::VaultCreate, on_stage);
            if let Err(e) = self.vault.create() {
                set(self, Stage::Failed(format!("could not create the vault: {e}")), on_stage);
                return self.stage.clone();
            }
        }
        if let Err(e) = self.vault.mount() {
            set(self, Stage::Failed(format!("could not mount the vault: {e}")), on_stage);
            return self.stage.clone();
        }
        if let Err(e) = self.init_workspace() {
            set(self, Stage::Failed(format!("could not set up the workspace: {e}")), on_stage);
            return self.stage.clone();
        }
        self.touch();
        set(self, Stage::Ready, on_stage);
        self.stage.clone()
    }

    /// Lock now (user choice, suspend, quit). Busy leaves the vault mounted and the stage Ready.
    pub fn lock(&mut self) -> Result<(), VaultError> {
        self.vault.unmount_retrying(5, Duration::from_millis(400))?;
        self.stage = Stage::Locked;
        Ok(())
    }

    /// Idle check: lock when nothing touched the app for `idle_minutes`. Returns Err(Busy) when
    /// something still holds a file open; the caller shows "vault still busy" and tries again later.
    pub fn check_idle(&mut self, now: Instant) -> Result<bool, VaultError> {
        if self.stage != Stage::Ready || now.duration_since(self.last_activity) < Duration::from_secs(self.persisted.idle_minutes * 60) {
            return Ok(false);
        }
        self.lock().map(|_| true)
    }

    pub fn unlock(&mut self) -> Stage {
        if self.stage != Stage::Locked {
            return self.stage.clone();
        }
        self.stage = match self.vault.mount() {
            Ok(()) => {
                self.touch();
                Stage::Ready
            }
            Err(e) => Stage::Failed(format!("could not unlock the vault: {e}")),
        };
        self.stage.clone()
    }

    pub fn on_suspend(&mut self) -> Result<(), VaultError> {
        if self.stage == Stage::Ready {
            self.lock()
        } else {
            Ok(())
        }
    }

    pub fn on_quit(&mut self) -> Result<(), VaultError> {
        self.vault.unmount_retrying(5, Duration::from_millis(400))
    }

    /// Delete the vault, its Keychain entry and the app data. Only with the exact confirmation word.
    pub fn uninstall(&mut self, confirm: &str) -> Result<(), String> {
        if confirm != "DELETE" {
            return Err("type DELETE to confirm".into());
        }
        self.vault.destroy().map_err(|e| e.to_string())?;
        let _ = std::fs::remove_file(self.data_dir.join("lifecycle.json"));
        self.persisted = Persisted::default();
        self.stage = Stage::Onboard;
        Ok(())
    }
}

/// Ollama over its local HTTP API (loopback, std sockets).
pub struct LocalOllama {
    pub port: u16,
}

impl LocalOllama {
    fn request(&self, method: &str, path: &str, body: &str) -> Option<(u16, String)> {
        use std::io::{Read, Write};
        let mut s = std::net::TcpStream::connect_timeout(&([127, 0, 0, 1], self.port).into(), Duration::from_millis(800)).ok()?;
        s.set_read_timeout(Some(Duration::from_secs(3600))).ok()?;
        write!(s, "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).ok()?;
        let mut out = String::new();
        s.read_to_string(&mut out).ok()?;
        let status = out.split_whitespace().nth(1)?.parse().ok()?;
        Some((status, out.split("\r\n\r\n").nth(1).unwrap_or("").to_string()))
    }
}

impl Ollama for LocalOllama {
    fn up(&self) -> bool {
        crate::sidecar::http_status(self.port, "/api/version", None, Duration::from_millis(800)) == Some(200)
    }

    fn installed(&self) -> Vec<String> {
        let Some((200, body)) = self.request("GET", "/api/tags", "") else { return vec![] };
        let v: serde_json::Value = serde_json::from_str(body.trim()).unwrap_or_default();
        v["models"].as_array().map(|a| a.iter().filter_map(|m| m["name"].as_str().map(String::from)).collect()).unwrap_or_default()
    }

    fn pull(&self, model: &str, progress: &mut dyn FnMut(u8)) -> Result<(), String> {
        let body = serde_json::json!({ "name": model, "stream": true }).to_string();
        let Some((status, text)) = self.request("POST", "/api/pull", &body) else { return Err("Ollama did not answer".into()) };
        if status != 200 {
            return Err(format!("Ollama returned {status}"));
        }
        let mut ok = false;
        for line in text.lines().filter(|l| l.trim_start().starts_with('{')) {
            let v: serde_json::Value = serde_json::from_str(line).unwrap_or_default();
            if let Some(e) = v["error"].as_str() {
                return Err(e.to_string());
            }
            if let (Some(c), Some(t)) = (v["completed"].as_u64(), v["total"].as_u64()) {
                if t > 0 {
                    progress((c * 100 / t).min(100) as u8);
                }
            }
            ok |= v["status"] == "success";
        }
        if ok { Ok(()) } else { Err("pull did not finish".into()) }
    }
}

pub fn data_dir_for(app_local: &Path) -> PathBuf {
    app_local.join("com.clixen.app")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::vault::VaultConfig;
    use std::cell::RefCell;
    use std::process::{Command, Stdio};
    use std::rc::Rc;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::sync::{Arc, Mutex};

    static N: AtomicU32 = AtomicU32::new(0);

    #[derive(Clone, Default)]
    struct FakeOllama {
        up: Arc<Mutex<bool>>,
        have: Arc<Mutex<Vec<String>>>,
        fail_pull: Arc<Mutex<bool>>,
        pulled: Arc<Mutex<Vec<String>>>,
    }

    impl Ollama for FakeOllama {
        fn up(&self) -> bool {
            *self.up.lock().unwrap()
        }
        fn installed(&self) -> Vec<String> {
            self.have.lock().unwrap().clone()
        }
        fn pull(&self, model: &str, progress: &mut dyn FnMut(u8)) -> Result<(), String> {
            if *self.fail_pull.lock().unwrap() {
                return Err("network down".into());
            }
            progress(50);
            self.pulled.lock().unwrap().push(model.into());
            self.have.lock().unwrap().push(model.into());
            Ok(())
        }
    }

    struct Fx {
        lc: Lifecycle,
        ollama: FakeOllama,
        dir: PathBuf,
    }

    fn fx() -> Fx {
        let id = format!("{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst));
        let dir = std::env::temp_dir().join(format!("clixen-lc-test-{id}"));
        std::fs::create_dir_all(&dir).unwrap();
        let vault = VaultManager::new(VaultConfig {
            image: dir.join("vault.sparseimage"),
            mountpoint: dir.join("mnt"),
            service: format!("clixen.test.lc.{id}"),
            account: "test".into(),
            size: "32m".into(),
        });
        let ollama = FakeOllama::default();
        *ollama.up.lock().unwrap() = true;
        let lc = Lifecycle::new(vault, Box::new(ollama.clone()), vec!["gemma4:12b".into(), "nomic-embed-text".into()], dir.clone());
        Fx { lc, ollama, dir }
    }

    impl Drop for Fx {
        fn drop(&mut self) {
            let _ = Command::new("/usr/bin/hdiutil").args(["detach", "-force", &self.lc.vault.cfg.mountpoint.to_string_lossy()]).output();
            let _ = Command::new("/usr/bin/security").args(["delete-generic-password", "-s", &self.lc.vault.cfg.service, "-a", "test"]).output();
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    fn run(lc: &mut Lifecycle) -> (Stage, Vec<Stage>) {
        let seen = Rc::new(RefCell::new(vec![]));
        let s2 = seen.clone();
        let end = lc.advance(&mut |s| s2.borrow_mut().push(s.clone()));
        let v = seen.borrow().clone();
        (end, v)
    }

    #[test]
    fn fresh_install_holds_at_onboarding_until_accepted() {
        let mut f = fx();
        assert_eq!(*f.lc.stage(), Stage::Onboard);
        assert_eq!(run(&mut f.lc).0, Stage::Onboard);
        assert_eq!(f.lc.vault.status(), VaultStatus::NotCreated, "nothing may be created before consent");
    }

    #[test]
    fn fresh_install_walks_models_vault_workspace_to_ready() {
        let mut f = fx();
        f.lc.accept_onboarding();
        let (end, seen) = run(&mut f.lc);
        assert_eq!(end, Stage::Ready);
        assert!(seen.contains(&Stage::Models("gemma4:12b".into(), 0)));
        assert!(seen.contains(&Stage::VaultCreate));
        assert_eq!(*f.ollama.pulled.lock().unwrap(), vec!["gemma4:12b", "nomic-embed-text"]);
        assert_eq!(f.lc.vault.status(), VaultStatus::Mounted);
        assert!(f.lc.vault.root().join("workspace").is_dir() && f.lc.vault.root().join(".clixen-vault").exists());
    }

    #[test]
    fn ollama_down_stops_with_ollama_missing_then_resumes() {
        let mut f = fx();
        f.lc.accept_onboarding();
        *f.ollama.up.lock().unwrap() = false;
        assert_eq!(run(&mut f.lc).0, Stage::OllamaMissing);
        assert_eq!(f.lc.vault.status(), VaultStatus::NotCreated);
        *f.ollama.up.lock().unwrap() = true;
        assert_eq!(run(&mut f.lc).0, Stage::Ready);
    }

    #[test]
    fn failed_pull_reports_the_error_and_retries_cleanly() {
        let mut f = fx();
        f.lc.accept_onboarding();
        *f.ollama.fail_pull.lock().unwrap() = true;
        let (end, _) = run(&mut f.lc);
        assert!(matches!(&end, Stage::Failed(m) if m.contains("gemma4:12b") && m.contains("network down")));
        assert_eq!(f.lc.vault.status(), VaultStatus::NotCreated);
        *f.ollama.fail_pull.lock().unwrap() = false;
        assert_eq!(run(&mut f.lc).0, Stage::Ready);
    }

    #[test]
    fn relaunch_skips_onboarding_and_remounts_existing_vault() {
        let mut f = fx();
        f.lc.accept_onboarding();
        run(&mut f.lc);
        std::fs::write(f.lc.vault.root().join("workspace/note.txt"), "persist").unwrap();
        f.lc.on_quit().unwrap();
        assert_eq!(f.lc.vault.status(), VaultStatus::Locked);

        let vault = VaultManager::new(f.lc.vault.cfg.clone());
        let mut again = Lifecycle::new(vault, Box::new(f.ollama.clone()), f.lc.models.clone(), f.dir.clone());
        assert_eq!(*again.stage(), Stage::VaultCreate, "onboarded flag must persist");
        assert_eq!(run(&mut again).0, Stage::Ready);
        assert_eq!(std::fs::read_to_string(again.vault.root().join("workspace/note.txt")).unwrap(), "persist");
        assert!(f.ollama.pulled.lock().unwrap().len() == 2, "models are not pulled twice");
    }

    #[test]
    fn idle_timeout_locks_and_unlock_restores() {
        let mut f = fx();
        f.lc.accept_onboarding();
        f.lc.set_idle_minutes(1);
        run(&mut f.lc);
        let t0 = Instant::now();
        assert_eq!(f.lc.check_idle(t0 + Duration::from_secs(30)), Ok(false));
        assert_eq!(f.lc.check_idle(t0 + Duration::from_secs(61)), Ok(true));
        assert_eq!(*f.lc.stage(), Stage::Locked);
        assert_eq!(f.lc.vault.status(), VaultStatus::Locked);
        assert_eq!(f.lc.unlock(), Stage::Ready);
        assert_eq!(f.lc.vault.status(), VaultStatus::Mounted);
    }

    #[test]
    fn busy_vault_at_idle_stays_mounted_and_loses_nothing() {
        let mut f = fx();
        f.lc.accept_onboarding();
        f.lc.set_idle_minutes(1);
        run(&mut f.lc);
        let file = f.lc.vault.root().join("workspace/live.log");
        std::fs::write(&file, "in flight").unwrap();
        let mut holder = Command::new("/usr/bin/tail").arg("-f").arg(&file).stdout(Stdio::null()).spawn().unwrap();
        std::thread::sleep(Duration::from_millis(400));

        assert_eq!(f.lc.check_idle(Instant::now() + Duration::from_secs(61)), Err(VaultError::Busy));
        assert_eq!(*f.lc.stage(), Stage::Ready);
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "in flight");

        holder.kill().unwrap();
        holder.wait().unwrap();
        assert_eq!(f.lc.check_idle(Instant::now() + Duration::from_secs(61)), Ok(true));
    }

    #[test]
    fn suspend_locks_only_when_ready() {
        let mut f = fx();
        f.lc.accept_onboarding();
        assert!(f.lc.on_suspend().is_ok());
        assert_eq!(f.lc.vault.status(), VaultStatus::NotCreated);
        run(&mut f.lc);
        f.lc.on_suspend().unwrap();
        assert_eq!(*f.lc.stage(), Stage::Locked);
    }

    #[test]
    fn uninstall_needs_the_exact_word_then_removes_everything() {
        let mut f = fx();
        f.lc.accept_onboarding();
        run(&mut f.lc);
        assert!(f.lc.uninstall("delete").is_err());
        assert!(f.lc.uninstall("").is_err());
        assert_eq!(f.lc.vault.status(), VaultStatus::Mounted, "a wrong word must delete nothing");

        f.lc.uninstall("DELETE").unwrap();
        assert_eq!(f.lc.vault.status(), VaultStatus::NotCreated);
        assert!(!f.dir.join("lifecycle.json").exists());
        assert_eq!(*f.lc.stage(), Stage::Onboard);
    }
}
