/**
 * Clixen dev bridge — service worker.
 *
 * Scope, by design (ADR 0006): `activeTab` + `scripting`, so nothing is readable
 * until the user acts, and only on the tab they are looking at. This file never
 * holds a credential; the localhost token is entered once by the user and kept in
 * extension storage.
 *
 * Transport: poll the local core for one command, execute it in the page, post the
 * result back. No inbound port is opened, and the core never reaches into Chrome.
 */

const DEFAULT_CORE = "http://127.0.0.1:9234";
let running = false;

async function config() {
  const { core, token } = await chrome.storage.local.get(["core", "token"]);
  return { core: (core || DEFAULT_CORE).replace(/\/$/, ""), token: token || "" };
}

async function call(path, options = {}) {
  const { core, token } = await config();
  const res = await fetch(core + path, {
    ...options,
    headers: { "x-clixen-token": token, "content-type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

/** Run `fn` in the active tab. The tab must have been granted by the user. */
async function inActiveTab(fn, args) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("no active tab");
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: fn,
    args: [args || {}],
  });
  return result ? result.result : null;
}

/** Page-side probe. Must stay self-contained: it is serialised into the page. */
function pageProbe(args) {
  const KEY = "__clixen_log";
  if (!window[KEY]) {
    const log = [];
    window[KEY] = log;
    const keep = (type) => (orig) =>
      function (...a) {
        try {
          log.push({
            type,
            text: a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ").slice(0, 500),
            at: Date.now(),
          });
          if (log.length > 300) log.shift();
        } catch (e) {}
        return orig.apply(console, a);
      };
    ["log", "info", "warn", "error", "debug"].forEach((t) => {
      console[t] = keep(t)(console[t]);
    });
    window.addEventListener("error", (e) =>
      log.push({ type: "pageerror", text: String(e.message || e).slice(0, 500), at: Date.now() })
    );
    window.addEventListener("unhandledrejection", (e) =>
      log.push({ type: "pageerror", text: "unhandled rejection: " + String(e.reason).slice(0, 400), at: Date.now() })
    );
  }

  const kind = (args && args.kind) || "dom";

  if (kind === "console") {
    const level = (args.level || "").toLowerCase();
    const rows = (window[KEY] || []).filter((m) => !level || m.type === level);
    return {
      count: rows.length,
      errors: rows.filter((m) => m.type === "error" || m.type === "pageerror").length,
      entries: rows.slice(-(args.limit || 50)),
    };
  }

  if (kind === "text") {
    return { text: (document.body ? document.body.innerText : "").slice(0, args.limit || 8000) };
  }

  if (kind === "click") {
    const want = (args.text || "").trim().toLowerCase();
    if (!want) return { ok: false, error: "click needs text" };
    const nodes = [...document.querySelectorAll("button,a,[role=button],input[type=button],input[type=submit]")];
    const hits = nodes.filter((n) => {
      const label = (n.innerText || n.value || n.getAttribute("aria-label") || "").trim().toLowerCase();
      const r = n.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && label.includes(want);
    });
    if (!hits.length) return { ok: false, error: `no visible control matching '${args.text}'` };
    hits.sort((a, b) => (a.innerText || "").length - (b.innerText || "").length);
    const target = hits[0];
    const label = (target.innerText || target.value || "").trim().slice(0, 80);
    target.click();
    return { ok: true, clicked: label };
  }

  const queue = [document.body || document.documentElement];
  const out = [];
  while (queue.length && out.length < (args.limit || 120)) {
    const el = queue.shift();
    if (!el || !el.children) continue;
    for (const child of el.children) {
      const r = child.getBoundingClientRect();
      const style = getComputedStyle(child);
      if (!(r.width > 2 && r.height > 2) || style.visibility === "hidden" || style.display === "none") continue;
      out.push({
        tag: child.tagName.toLowerCase(),
        role: child.getAttribute("role") || "",
        label: (child.innerText || child.getAttribute("aria-label") || "").trim().slice(0, 80),
        href: child.getAttribute("href") || "",
      });
      queue.push(child);
    }
  }
  return {
    url: location.href,
    title: document.title,
    elements: out,
    consent: [...document.querySelectorAll("button,[role=button]")]
      .map((b) => (b.innerText || "").trim())
      .filter((t) => /accept|reject|consent|cookie|agree/i.test(t))
      .slice(0, 6),
  };
}


async function runCommand(cmd) {
  const { action, args } = cmd;
  if (action === "ping") {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return { ok: true, data: { url: tab && tab.url, title: tab && tab.title } };
  }
  if (action === "probe") {
    return { ok: true, data: await inActiveTab(pageProbe, args || {}) };
  }
  return { ok: false, error: `unknown action '${action}'` };
}

async function pollOnce() {
  const { command } = await call("/ext/poll");
  if (!command) return;
  let payload;
  try {
    const res = await runCommand(command);
    payload = { id: command.id, ok: res.ok !== false, data: res.data, error: res.error || "" };
  } catch (e) {
    payload = { id: command.id, ok: false, data: null, error: String(e).slice(0, 300) };
  }
  await call("/ext/result", { method: "POST", body: JSON.stringify(payload) });
}

async function loop() {
  if (running) return;
  running = true;
  while (running) {
    try {
      await pollOnce();
    } catch (e) {
      // Core not running, token missing, or the tab is gone: back off, never spin.
      await new Promise((r) => setTimeout(r, 1500));
    }
    await new Promise((r) => setTimeout(r, 900));
  }
}

chrome.runtime.onStartup.addListener(loop);
chrome.runtime.onInstalled.addListener(loop);
chrome.action.onClicked.addListener(async () => {
  // The toolbar click IS the user gesture that grants this tab.
  await loop();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.id) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: pageProbe, args: [{}] });
    } catch (e) {}
  }
  await pollOnce().catch(() => {});
});
