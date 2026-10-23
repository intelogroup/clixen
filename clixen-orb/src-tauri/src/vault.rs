//! Encrypted vault: one AES-256 sparse image, mounted on demand, passphrase in the Keychain.
//! Shells out to `hdiutil` and `security` (same convention as tools-harness/tools/vault.py).
//! The passphrase reaches `hdiutil` on stdin; it reaches `security add-generic-password` as an
//! argument for the few milliseconds of that call (same user only), and is never logged or written to disk.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread::sleep;
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct VaultConfig {
    /// Full image path, including the `.sparseimage` extension.
    pub image: PathBuf,
    pub mountpoint: PathBuf,
    /// Keychain service, e.g. `clixen.vault.<id>`.
    pub service: String,
    pub account: String,
    /// `hdiutil -size` value; a sparse image only grows as data is written.
    pub size: String,
}

#[derive(Debug, PartialEq, Eq)]
pub enum VaultError {
    /// Something still has a file open inside the vault. Nothing was dropped; retry later.
    Busy,
    Missing,
    Other(String),
}

impl std::fmt::Display for VaultError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VaultError::Busy => write!(f, "vault still busy"),
            VaultError::Missing => write!(f, "vault image not found"),
            VaultError::Other(m) => write!(f, "{m}"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VaultStatus {
    NotCreated,
    Locked,
    Mounted,
}

pub struct VaultManager {
    pub cfg: VaultConfig,
}

fn out_text(o: &std::process::Output) -> String {
    let t = String::from_utf8_lossy(&o.stderr).trim().to_string();
    if t.is_empty() {
        String::from_utf8_lossy(&o.stdout).trim().to_string()
    } else {
        t
    }
}

/// Run `hdiutil` with the passphrase on stdin.
fn hdiutil_stdin(args: &[&str], pass: &str) -> Result<std::process::Output, VaultError> {
    let mut child = Command::new("/usr/bin/hdiutil")
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| VaultError::Other(format!("hdiutil: {e}")))?;
    child.stdin.take().expect("piped").write_all(pass.as_bytes()).map_err(|e| VaultError::Other(e.to_string()))?;
    child.wait_with_output().map_err(|e| VaultError::Other(e.to_string()))
}

impl VaultManager {
    pub fn new(cfg: VaultConfig) -> Self {
        Self { cfg }
    }

    pub fn status(&self) -> VaultStatus {
        if self.is_mounted() {
            VaultStatus::Mounted
        } else if self.cfg.image.exists() {
            VaultStatus::Locked
        } else {
            VaultStatus::NotCreated
        }
    }

    fn is_mounted(&self) -> bool {
        let Ok(o) = Command::new("/sbin/mount").output() else { return false };
        // `mount` prints the resolved path (/tmp -> /private/tmp), so compare on the canonical form
        let mp = std::fs::canonicalize(&self.cfg.mountpoint).unwrap_or_else(|_| self.cfg.mountpoint.clone());
        let needle = format!(" on {} (", mp.display());
        String::from_utf8_lossy(&o.stdout).contains(&needle)
    }

    fn passphrase(&self) -> Result<String, VaultError> {
        let o = Command::new("/usr/bin/security")
            .args(["find-generic-password", "-s", &self.cfg.service, "-a", &self.cfg.account, "-w"])
            .output()
            .map_err(|e| VaultError::Other(format!("security: {e}")))?;
        if !o.status.success() {
            return Err(VaultError::Other(format!("no vault passphrase in the Keychain ({})", out_text(&o))));
        }
        Ok(String::from_utf8_lossy(&o.stdout).trim_end_matches('\n').to_string())
    }

    fn new_passphrase() -> Result<String, VaultError> {
        let o = Command::new("/usr/bin/openssl").args(["rand", "-base64", "48"]).output().map_err(|e| VaultError::Other(format!("openssl: {e}")))?;
        if !o.status.success() {
            return Err(VaultError::Other("openssl rand failed".into()));
        }
        Ok(String::from_utf8_lossy(&o.stdout).split_whitespace().collect())
    }

    /// Create the encrypted image and store a fresh passphrase in the Keychain. Refuses to overwrite.
    pub fn create(&self) -> Result<(), VaultError> {
        if self.cfg.image.exists() {
            return Err(VaultError::Other("vault already exists".into()));
        }
        if let Some(dir) = self.cfg.image.parent() {
            std::fs::create_dir_all(dir).map_err(|e| VaultError::Other(e.to_string()))?;
        }
        let pass = Self::new_passphrase()?;
        let stem = self.cfg.image.with_extension(""); // hdiutil appends .sparseimage itself
        let o = hdiutil_stdin(
            &["create", "-size", &self.cfg.size, "-type", "SPARSE", "-fs", "APFS", "-volname", "ClixenVault", "-encryption", "AES-256", "-stdinpass", &stem.to_string_lossy()],
            &pass,
        )?;
        if !o.status.success() {
            return Err(VaultError::Other(format!("hdiutil create failed: {}", out_text(&o))));
        }
        let k = Command::new("/usr/bin/security")
            .args(["add-generic-password", "-U", "-s", &self.cfg.service, "-a", &self.cfg.account, "-w", &pass])
            .output()
            .map_err(|e| VaultError::Other(format!("security: {e}")))?;
        if !k.status.success() {
            let _ = std::fs::remove_file(&self.cfg.image); // an image whose passphrase was never saved is unrecoverable
            return Err(VaultError::Other(format!("could not save the passphrase to the Keychain: {}", out_text(&k))));
        }
        let _ = Command::new("/usr/bin/chflags").arg("hidden").arg(&self.cfg.image).status();
        Ok(())
    }

    pub fn mount(&self) -> Result<(), VaultError> {
        if !self.cfg.image.exists() {
            return Err(VaultError::Missing);
        }
        if self.is_mounted() {
            return Ok(());
        }
        std::fs::create_dir_all(&self.cfg.mountpoint).map_err(|e| VaultError::Other(e.to_string()))?;
        let pass = self.passphrase()?;
        let o = hdiutil_stdin(
            &["attach", &self.cfg.image.to_string_lossy(), "-stdinpass", "-nobrowse", "-mountpoint", &self.cfg.mountpoint.to_string_lossy()],
            &pass,
        )?;
        if o.status.success() {
            Ok(())
        } else {
            Err(VaultError::Other(format!("hdiutil attach failed: {}", out_text(&o))))
        }
    }

    /// Detach once. A busy volume returns `Busy` and stays mounted: never forced.
    pub fn unmount(&self) -> Result<(), VaultError> {
        if !self.is_mounted() {
            return Ok(());
        }
        let o = Command::new("/usr/bin/hdiutil").args(["detach", &self.cfg.mountpoint.to_string_lossy()]).output().map_err(|e| VaultError::Other(e.to_string()))?;
        if o.status.success() {
            return Ok(());
        }
        let t = out_text(&o);
        if t.contains("busy") || t.contains("Resource busy") {
            Err(VaultError::Busy)
        } else {
            Err(VaultError::Other(format!("hdiutil detach failed: {t}")))
        }
    }

    /// Detach, retrying while busy. Returns `Busy` after the last try; data is never dropped.
    pub fn unmount_retrying(&self, tries: u32, wait: Duration) -> Result<(), VaultError> {
        let mut last = Ok(());
        for i in 0..tries.max(1) {
            last = self.unmount();
            if last != Err(VaultError::Busy) {
                return last;
            }
            if i + 1 < tries {
                sleep(wait);
            }
        }
        last
    }

    /// Remove the image and its Keychain entry. Callers must have confirmed with the user first.
    pub fn destroy(&self) -> Result<(), VaultError> {
        self.unmount()?;
        let _ = Command::new("/usr/bin/security").args(["delete-generic-password", "-s", &self.cfg.service, "-a", &self.cfg.account]).output();
        if self.cfg.image.exists() {
            std::fs::remove_file(&self.cfg.image).map_err(|e| VaultError::Other(e.to_string()))?;
        }
        Ok(())
    }

    pub fn root(&self) -> &Path {
        &self.cfg.mountpoint
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Child;
    use std::sync::atomic::{AtomicU32, Ordering};

    static N: AtomicU32 = AtomicU32::new(0);

    /// A vault in its own temp dir under a throwaway Keychain service; cleaned up on drop.
    struct Fixture {
        vm: VaultManager,
        dir: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let id = format!("{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst));
            let dir = std::env::temp_dir().join(format!("clixen-vault-test-{id}"));
            std::fs::create_dir_all(&dir).unwrap();
            let vm = VaultManager::new(VaultConfig {
                image: dir.join("vault.sparseimage"),
                mountpoint: dir.join("mnt"),
                service: format!("clixen.test.vault.{id}"),
                account: "test".into(),
                size: "32m".into(),
            });
            Self { vm, dir }
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = Command::new("/usr/bin/hdiutil").args(["detach", "-force", &self.vm.cfg.mountpoint.to_string_lossy()]).output();
            let _ = Command::new("/usr/bin/security").args(["delete-generic-password", "-s", &self.vm.cfg.service, "-a", "test"]).output();
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    #[test]
    fn status_walks_not_created_locked_mounted_locked() {
        let f = Fixture::new();
        assert_eq!(f.vm.status(), VaultStatus::NotCreated);
        f.vm.create().unwrap();
        assert_eq!(f.vm.status(), VaultStatus::Locked);
        f.vm.mount().unwrap();
        assert_eq!(f.vm.status(), VaultStatus::Mounted);
        f.vm.unmount().unwrap();
        assert_eq!(f.vm.status(), VaultStatus::Locked);
    }

    #[test]
    fn data_round_trips_and_is_unreadable_while_locked() {
        let f = Fixture::new();
        f.vm.create().unwrap();
        f.vm.mount().unwrap();
        let marker = "CLIXEN-VAULT-MARKER-4f1c9a";
        std::fs::write(f.vm.root().join("secret.txt"), marker).unwrap();
        f.vm.unmount().unwrap();

        assert!(!f.vm.root().join("secret.txt").exists(), "file visible while the vault is locked");
        let raw = std::fs::read(&f.vm.cfg.image).unwrap();
        assert!(!raw.windows(marker.len()).any(|w| w == marker.as_bytes()), "plaintext found in the image file");

        f.vm.mount().unwrap();
        assert_eq!(std::fs::read_to_string(f.vm.root().join("secret.txt")).unwrap(), marker);
    }

    #[test]
    fn create_refuses_to_overwrite_and_mount_is_idempotent() {
        let f = Fixture::new();
        f.vm.create().unwrap();
        assert!(f.vm.create().is_err());
        f.vm.mount().unwrap();
        f.vm.mount().unwrap();
        f.vm.unmount().unwrap();
        f.vm.unmount().unwrap();
    }

    #[test]
    fn mount_without_image_or_passphrase_fails_cleanly() {
        let f = Fixture::new();
        assert_eq!(f.vm.mount(), Err(VaultError::Missing));
        f.vm.create().unwrap();
        let _ = Command::new("/usr/bin/security").args(["delete-generic-password", "-s", &f.vm.cfg.service, "-a", "test"]).output();
        assert!(matches!(f.vm.mount(), Err(VaultError::Other(m)) if m.contains("Keychain")));
    }

    fn hold_open(path: &Path) -> Child {
        // `tail -f` keeps a file open inside the volume, like a write in flight
        Command::new("/usr/bin/tail").arg("-f").arg(path).stdout(Stdio::null()).spawn().unwrap()
    }

    #[test]
    fn busy_detach_reports_busy_keeps_data_then_succeeds_once_released() {
        let f = Fixture::new();
        f.vm.create().unwrap();
        f.vm.mount().unwrap();
        let file = f.vm.root().join("open.log");
        std::fs::write(&file, "keep me").unwrap();
        let mut holder = hold_open(&file);
        sleep(Duration::from_millis(400));

        assert_eq!(f.vm.unmount(), Err(VaultError::Busy));
        assert_eq!(f.vm.unmount_retrying(2, Duration::from_millis(200)), Err(VaultError::Busy));
        assert_eq!(f.vm.status(), VaultStatus::Mounted);
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "keep me");

        holder.kill().unwrap();
        holder.wait().unwrap();
        f.vm.unmount_retrying(5, Duration::from_millis(300)).unwrap();
        assert_eq!(f.vm.status(), VaultStatus::Locked);
    }

    #[test]
    fn destroy_removes_image_and_keychain_entry() {
        let f = Fixture::new();
        f.vm.create().unwrap();
        f.vm.mount().unwrap();
        f.vm.destroy().unwrap();
        assert_eq!(f.vm.status(), VaultStatus::NotCreated);
        assert!(f.vm.passphrase().is_err());
    }
}
