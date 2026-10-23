// Setup / settings window. It only calls lifecycle_* commands (see capabilities/setup.json).
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

const app = document.getElementById("app");
let info = null;
let settings = false;
let timer = null;

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mins = (s) => (s == null ? "" : s < 90 ? `${s}s` : `${Math.round(s / 60)}m`);

function screen(stage, detail) {
  switch (stage) {
    case "onboard":
      return `<div class="card" data-stage="onboard">
        <div class="eyebrow">First run</div>
        <h1>Your documents stay on your machine.</h1>
        <p>Clixen runs on this Mac. Everything you work with lives in an encrypted vault and is read by models that run here.</p>
        <div class="box"><b>What it protects.</b><ul>
          <li><b>Encryption at rest.</b> The vault is AES-256; its passphrase lives in your Keychain.</li>
          <li><b>Offline by default.</b> Nothing leaves this Mac unless you turn on a cloud transport.</li>
          <li><b>Auto-lock.</b> The vault locks when idle, on sleep and on quit.</li></ul></div>
        <div class="box"><b>What it cannot protect.</b><ul>
          <li>Apps you gave Full Disk Access can read the vault while it is unlocked.</li>
          <li>Files you share yourself (AirDrop, email, iCloud) leave the vault.</li>
          <li>A stolen Mac: turn on FileVault.</li></ul></div>
        <div class="row"><button class="primary" id="accept">Continue</button></div></div>`;
    case "ollama_missing":
      return `<div class="card" data-stage="ollama_missing">
        <div class="eyebrow">Setup</div><h1>Ollama is not running.</h1>
        <p>Clixen needs Ollama to run its models on this Mac. Install it from ollama.com, open it once, then check again.</p>
        <div class="row"><button class="primary" id="retry">Check again</button></div></div>`;
    case "models": {
      const [name, pct] = detail;
      return `<div class="card" data-stage="models"><div class="eyebrow">Setup</div><h1>Downloading models</h1>
        <p>${esc(name)}. This is a one-time download.</p>
        <div class="bar"><i style="width:${pct}%"></i></div><div class="eyebrow">${pct}%</div></div>`;
    }
    case "vault_create":
      return `<div class="card center" data-stage="vault_create"><div class="spin"></div><h1>Preparing your encrypted vault</h1><p style="margin:0 auto">Creating and unlocking it. This takes a few seconds.</p></div>`;
    case "failed":
      return `<div class="card" data-stage="failed"><div class="eyebrow">Setup</div><h1>Something went wrong.</h1>
        <p class="err">${esc(detail)}</p><div class="row"><button class="primary" id="retry">Try again</button></div></div>`;
    case "locked":
      return `<div class="card center" data-stage="locked"><h1>Vault locked</h1><p style="margin:0 auto 14px">It locked to keep your documents safe. Unlock to continue.</p>
        <div class="row" style="justify-content:center"><button class="primary" id="unlock">Unlock</button></div></div>`;
    default:
      return `<div class="card center" data-stage="${esc(stage)}"><div class="spin"></div></div>`;
  }
}

function settingsScreen() {
  const i = info || {};
  return `<div class="card" data-stage="settings"><div class="eyebrow">Settings</div>
    <h1>Privacy and vault</h1>
    <p><span class="badge" id="badge">Encrypted · mounted ${mins(i.mounted_secs)}</span></p>
    <label for="idle">Lock after idle (minutes)</label>
    <select id="idle">${[1, 5, 15, 30, 60].map((m) => `<option value="${m}"${m === i.idle_minutes ? " selected" : ""}>${m}</option>`).join("")}</select>
    <div class="row" style="justify-content:flex-start"><button id="lock">Lock now</button><button id="close">Close</button></div>
    <div class="box" style="margin-top:20px"><b>Uninstall data</b>
      <p style="margin:6px 0 8px">Deletes the vault and its Keychain entry. Your documents inside the vault are gone for good. Type DELETE to confirm.</p>
      <input id="confirm" placeholder="DELETE" autocomplete="off" /> <button class="danger" id="uninstall" disabled>Delete everything</button>
      <div class="err" id="uerr"></div></div></div>`;
}

async function draw(stage) {
  const s = typeof stage === "string" ? { stage } : stage;
  app.innerHTML = settings && s.stage === "ready" ? settingsScreen() : s.stage === "ready" ? "" : screen(s.stage, s.detail);
  wire();
}

function wire() {
  const $ = (id) => document.getElementById(id);
  const run = (cmd, args) => invoke(cmd, args).then(refresh).catch((e) => { const u = $("uerr"); if (u) u.textContent = String(e); });
  $("accept")?.addEventListener("click", () => run("lifecycle_accept"));
  $("retry")?.addEventListener("click", () => run("lifecycle_advance"));
  $("unlock")?.addEventListener("click", () => run("lifecycle_unlock"));
  $("lock")?.addEventListener("click", () => run("lifecycle_lock").then(() => (settings = false)));
  $("close")?.addEventListener("click", () => { settings = false; invoke("lifecycle_touch"); window.close(); });
  $("idle")?.addEventListener("change", (e) => invoke("lifecycle_set_idle", { minutes: Number(e.target.value) }));
  $("confirm")?.addEventListener("input", (e) => { $("uninstall").disabled = e.target.value !== "DELETE"; });
  $("uninstall")?.addEventListener("click", () => run("lifecycle_uninstall", { confirm: $("confirm").value }));
}

async function refresh() {
  info = await invoke("lifecycle_info");
  draw(info.stage);
}

listen("lifecycle-stage", (e) => { settings = false; draw(e.payload); });
listen("lifecycle-settings", () => { settings = true; refresh(); });
listen("lifecycle-notice", (e) => { const el = document.getElementById("uerr"); if (el) el.textContent = e.payload; });

timer = setInterval(() => { const b = document.getElementById("badge"); if (b && info) { invoke("lifecycle_info").then((i) => { info = i; b.textContent = `Encrypted · mounted ${mins(i.mounted_secs)}`; }); } }, 5000);
refresh();
