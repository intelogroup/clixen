//! Sidecar supervisor: spawns the Clixen backend (`chat_ui.py` in dev, a frozen binary when shipped),
//! polls its health endpoint over loopback, and restarts it with backoff when it dies or hangs.
//! Synchronous on purpose (std only): callers run it on a worker thread, tests need no runtime.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct SidecarConfig {
    /// Program, then its arguments.
    pub cmd: Vec<String>,
    pub env: Vec<(String, String)>,
    pub port: u16,
    pub health_path: String,
    /// Sent as `x-clixen-token` when set.
    pub token: Option<String>,
    /// After a start, how long the sidecar gets to answer its first health check.
    pub startup_grace: Duration,
    /// Restarts allowed inside `window` before the supervisor gives up.
    pub max_restarts: u32,
    pub window: Duration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SidecarState {
    Stopped,
    Starting,
    Healthy,
    /// Gave up: too many restarts. The UI shows the failure instead of looping.
    Failed,
}

pub struct Sidecar {
    cfg: SidecarConfig,
    child: Option<Child>,
    started_at: Option<Instant>,
    restarts: Vec<Instant>,
    state: SidecarState,
}

/// GET over a raw loopback socket. Returns the HTTP status, or None when nothing answers.
pub fn http_status(port: u16, path: &str, token: Option<&str>, timeout: Duration) -> Option<u16> {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut s = TcpStream::connect_timeout(&addr, timeout).ok()?;
    s.set_read_timeout(Some(timeout)).ok()?;
    s.set_write_timeout(Some(timeout)).ok()?;
    let auth = token.map(|t| format!("x-clixen-token: {t}\r\n")).unwrap_or_default();
    let req = format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{auth}Connection: close\r\n\r\n");
    s.write_all(req.as_bytes()).ok()?;
    let mut buf = [0u8; 64];
    let n = s.read(&mut buf).ok()?;
    String::from_utf8_lossy(&buf[..n]).split_whitespace().nth(1)?.parse().ok()
}

impl Sidecar {
    pub fn new(cfg: SidecarConfig) -> Self {
        Self { cfg, child: None, started_at: None, restarts: vec![], state: SidecarState::Stopped }
    }

    pub fn state(&self) -> SidecarState {
        self.state
    }

    pub fn pid(&self) -> Option<u32> {
        self.child.as_ref().map(|c| c.id())
    }

    fn spawn(&mut self) -> std::io::Result<()> {
        let (prog, args) = self.cfg.cmd.split_first().ok_or_else(|| std::io::Error::other("empty sidecar command"))?;
        let child = Command::new(prog)
            .args(args)
            .envs(self.cfg.env.iter().cloned())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;
        self.child = Some(child);
        self.started_at = Some(Instant::now());
        self.state = SidecarState::Starting;
        Ok(())
    }

    pub fn start(&mut self) -> std::io::Result<()> {
        if self.child.is_some() {
            return Ok(());
        }
        self.spawn()
    }

    fn alive(&mut self) -> bool {
        match self.child.as_mut().map(|c| c.try_wait()) {
            Some(Ok(None)) => true,
            _ => false,
        }
    }

    pub fn healthy(&self) -> bool {
        http_status(self.cfg.port, &self.cfg.health_path, self.cfg.token.as_deref(), Duration::from_millis(800)) == Some(200)
    }

    /// One supervision step: call every second or so. Restarts a dead or unresponsive sidecar.
    pub fn tick(&mut self) -> SidecarState {
        if matches!(self.state, SidecarState::Stopped | SidecarState::Failed) {
            return self.state;
        }
        let alive = self.alive();
        if alive && self.healthy() {
            self.state = SidecarState::Healthy;
            return self.state;
        }
        let in_grace = alive && self.started_at.is_some_and(|t| t.elapsed() < self.cfg.startup_grace);
        if in_grace {
            return self.state;
        }
        self.restart()
    }

    fn restart(&mut self) -> SidecarState {
        self.kill_child();
        let now = Instant::now();
        self.restarts.retain(|t| now.duration_since(*t) < self.cfg.window);
        if self.restarts.len() as u32 >= self.cfg.max_restarts {
            self.state = SidecarState::Failed;
            return self.state;
        }
        self.restarts.push(now);
        if self.spawn().is_err() {
            self.state = SidecarState::Failed;
        }
        self.state
    }

    fn kill_child(&mut self) {
        if let Some(mut c) = self.child.take() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }

    /// Stop for good (app quit). Does not restart.
    pub fn stop(&mut self) {
        self.kill_child();
        self.state = SidecarState::Stopped;
    }

    /// Clear a `Failed` state so the user can retry from the UI.
    pub fn reset(&mut self) {
        self.stop();
        self.restarts.clear();
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        self.kill_child();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread::sleep;

    /// A tiny backend that answers 200 only with the right token, otherwise 403, like chat_ui.
    const SERVER: &str = r#"
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("x-clixen-token") == "t0k3n"
        self.send_response(200 if ok else 403); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"#;

    fn free_port() -> u16 {
        TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
    }

    fn cfg(port: u16, script: &str) -> SidecarConfig {
        SidecarConfig {
            cmd: vec!["/usr/bin/python3".into(), "-c".into(), script.into(), port.to_string()],
            env: vec![],
            port,
            health_path: "/health".into(),
            token: Some("t0k3n".into()),
            startup_grace: Duration::from_secs(5),
            max_restarts: 3,
            window: Duration::from_secs(60),
        }
    }

    fn wait_healthy(s: &mut Sidecar, secs: u64) -> bool {
        let end = Instant::now() + Duration::from_secs(secs);
        while Instant::now() < end {
            if s.tick() == SidecarState::Healthy {
                return true;
            }
            sleep(Duration::from_millis(200));
        }
        false
    }

    #[test]
    fn starts_and_reports_healthy_then_stops() {
        let port = free_port();
        let mut s = Sidecar::new(cfg(port, SERVER));
        s.start().unwrap();
        assert!(wait_healthy(&mut s, 10));
        s.stop();
        assert_eq!(s.state(), SidecarState::Stopped);
        assert_eq!(http_status(port, "/health", Some("t0k3n"), Duration::from_millis(300)), None);
    }

    #[test]
    fn request_without_the_token_is_rejected() {
        let port = free_port();
        let mut s = Sidecar::new(cfg(port, SERVER));
        s.start().unwrap();
        assert!(wait_healthy(&mut s, 10));
        assert_eq!(http_status(port, "/health", None, Duration::from_secs(1)), Some(403));
        assert_eq!(http_status(port, "/health", Some("wrong"), Duration::from_secs(1)), Some(403));
    }

    #[test]
    fn killed_sidecar_is_restarted_with_a_new_pid() {
        let port = free_port();
        let mut s = Sidecar::new(cfg(port, SERVER));
        s.start().unwrap();
        assert!(wait_healthy(&mut s, 10));
        let first = s.pid().unwrap();
        libc_kill(first);
        sleep(Duration::from_millis(300));
        assert!(wait_healthy(&mut s, 10));
        assert_ne!(s.pid().unwrap(), first);
    }

    #[test]
    fn crash_loop_gives_up_instead_of_spinning() {
        let port = free_port();
        let mut c = cfg(port, "import sys; sys.exit(1)");
        c.startup_grace = Duration::from_millis(0);
        let mut s = Sidecar::new(c);
        s.start().unwrap();
        for _ in 0..20 {
            sleep(Duration::from_millis(100));
            if s.tick() == SidecarState::Failed {
                break;
            }
        }
        assert_eq!(s.state(), SidecarState::Failed);
        assert_eq!(s.tick(), SidecarState::Failed); // stays failed
        s.reset();
        assert_eq!(s.state(), SidecarState::Stopped);
    }

    #[test]
    fn hung_sidecar_past_grace_is_replaced() {
        let port = free_port();
        // listens but never answers: alive, not healthy
        let hang = "import socket,sys,time\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',int(sys.argv[1])));s.listen();time.sleep(600)";
        let mut c = cfg(port, hang);
        c.startup_grace = Duration::from_millis(500);
        let mut s = Sidecar::new(c);
        s.start().unwrap();
        let first = s.pid().unwrap();
        sleep(Duration::from_millis(700));
        s.tick();
        assert_ne!(s.pid().unwrap(), first);
        s.stop();
    }

    fn libc_kill(pid: u32) {
        let _ = Command::new("/bin/kill").args(["-9", &pid.to_string()]).status();
    }
}
