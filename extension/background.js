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
let reported = false;
let polls = 0;

async function config() {
  const { core, token } = await chrome.storage.local.get(["core", "token"]);
  if (token) return { core: (core || DEFAULT_CORE).replace(/\/$/, ""), token };
  // Dev builds ship a generated token.local.json (gitignored, mode 600) so the
  // bridge works without a manual paste. A store build has no such file and the
  // user enters the token once in Options.
  try {
    const res = await fetch(chrome.runtime.getURL("token.local.json"));
    if (res.ok) {
      const dev = await res.json();
      return { core: (dev.core || DEFAULT_CORE).replace(/\/$/, ""), token: dev.token || "" };
    }
  } catch (e) {}
  return { core: (core || DEFAULT_CORE).replace(/\/$/, ""), token: "" };
}

async function report(msg) {
  try {
    const { core, token } = await config();
    await fetch(core + "/ext/log?msg=" + encodeURIComponent(String(msg).slice(0, 280)), {
      headers: { "x-clixen-token": token },
    });
  } catch (e) {}
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
    // MAIN world: the page's own console is what we must intercept. In the default
    // ISOLATED world we would only patch a copy of console the page never uses
    // (measured: two probes, zero entries, while the page logged every 4 s).
    world: "MAIN",
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

  args = args || {};
  const kind = args.kind || "dom";

  if (kind === "console") {
    // Level aliases: the tool schema offers "error, warning, log, info" but the
    // browser reports "warn", and an uncaught exception is "pageerror" - not
    // "error". Matching literally made level="error" hide the very exceptions the
    // user was asking about, and level="warning" match nothing at all.
    const ALIAS = { error: ["error", "pageerror"], warning: ["warn"], warn: ["warn"] };
    const level = (args.level || "").toLowerCase();
    const wanted = ALIAS[level] || (level ? [level] : []);
    const all = window[KEY] || [];
    const rows = wanted.length ? all.filter((m) => wanted.includes(m.type)) : all;
    const types = {};
    for (const m of all) types[m.type] = (types[m.type] || 0) + 1;
    return {
      count: rows.length,
      total: all.length,
      filtered_by: level || null,
      types,
      errors: all.filter((m) => m.type === "error" || m.type === "pageerror").length,
      entries: rows.slice(-(args.limit || 50)),
    };
  }

  if (kind === "links") {
    // Downloadable URLs actually present in the tab, so the agent can hand a URL to
    // workspace_download instead of clicking the site's Download button and fighting
    // Chrome's Save As dialog. Measured 2026-10-25: the dialog is where the whole
    // "download the PDF" task died, twice, while the URL was sitting in an anchor.
    // Scans anchors, embedded frames and files the page fetched itself.
    const fileish = /\.(pdf|docx?|xlsx?|pptx?|csv|txt|zip|epub)(\?|#|$)/i;
    const want = (args.match || "").trim().toLowerCase();
    const seen = new Set();
    const out = [];
    const push = (href, text) => {
      try {
        const abs = new URL(href, document.baseURI).href;
        if (!/^https?:/i.test(abs) || seen.has(abs)) return;
        if (want) {
          if (!abs.toLowerCase().includes(want)) return;
        } else if (!fileish.test(abs)) return;
        seen.add(abs);
        out.push({ href: abs, text: (text || "").trim().slice(0, 120), file: fileish.test(abs) });
      } catch (e) { /* a malformed href is not worth failing the probe over */ }
    };
    for (const a of document.querySelectorAll("a[href]")) {
      push(a.getAttribute("href"), a.innerText || a.getAttribute("title") || "");
    }
    for (const el of document.querySelectorAll("[data-href],[data-url],[data-download]")) {
      push(el.getAttribute("data-href") || el.getAttribute("data-url") || el.getAttribute("data-download"), el.innerText || "");
    }
    try {
      const perf = performance.getEntriesByType("resource") || [];
      for (const e of perf) if (fileish.test(e.name)) push(e.name, "");
    } catch (e) { /* performance may be absent */ }
    return { count: out.length, matching: want || null, links: out.slice(0, args.limit || 40) };
  }

  if (kind === "viewport") {
    // "Is there more page?" is a DOM question, not an AX one. Chrome's accessibility tree
    // only exposes the viewport and reports no scroll extent, so an agent reading AX
    // cannot tell "this form has no Email field" from "it is one scroll down" — which is
    // exactly how it ended up typing into a field it had never brought on screen.
    const de = document.documentElement;
    const sh = Math.max(de ? de.scrollHeight : 0, document.body ? document.body.scrollHeight : 0);
    const ih = window.innerHeight;
    const y = window.scrollY || 0;
    const below = Math.max(0, Math.round(sh - (y + ih)));
    const above = Math.max(0, Math.round(y));
    // Fields and buttons that exist in the document but are not in view right now.
    const offscreen = [];
    for (const el of document.querySelectorAll("input,textarea,select,button,a[href]")) {
      if (offscreen.length >= 12) break;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const inView = r.bottom > 0 && r.top < ih;
      if (inView) continue;
      const label = (el.getAttribute("placeholder") || el.getAttribute("aria-label")
        || el.innerText || el.getAttribute("name") || el.tagName).trim().slice(0, 40);
      offscreen.push({ label, where: r.top >= ih ? "below" : "above", y: Math.round(r.top + y) });
    }
    return {
      scrollY: Math.round(y),
      innerHeight: ih,
      scrollHeight: sh,
      above,
      below,
      at_bottom: below < 4,
      offscreen,
      hint: below > 4
        ? "There is more page below the fold. Scroll before concluding a field is missing; scroll it into view, then act."
        : (above > 4 ? "There is more page above the fold." : "The whole page fits in the viewport."),
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
  const until = Date.now() + 25000; // stay awake for a burst, then let Chrome idle us
  while (running && Date.now() < until) {
    try {
      await pollOnce();
      reported = false;
      polls += 1;
      chrome.storage.local.set({ state: { polls, at: Date.now() } });
    } catch (e) {
      // Core not running, stale token, or the tab is gone: say so once, then back
      // off. Silence here is what made a dead poll invisible for minutes.
      if (!reported) {
        reported = true;
        chrome.storage.local.set({ state: { polls, at: Date.now(), error: String(e).slice(0, 200) } });
        await report("poll failed: " + String(e));
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
    await new Promise((r) => setTimeout(r, 700));
  }
  running = false;
}

// MV3 suspends the worker after ~30 s idle, which silently stops the poll loop
// (measured: reported connected at load, then timed out a minute later). An alarm
// is the only wake-up Chrome guarantees for an extension that isn't running, so
// the heartbeat re-arms it and the loop exits when Chrome suspends us.
function armHeartbeat() {
  chrome.alarms.create("clixen-poll", { periodInMinutes: 0.5 });
}

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "clixen-poll") loop();
});

// Self-start rather than relying on an event: a module worker can miss onInstalled
// at load (the event fires before top-level registration completes), which is
// exactly what happened here - the worker never armed its alarm and never polled.
report("extension started v0.1.0");
armHeartbeat();
loop();

chrome.runtime.onStartup.addListener(() => { armHeartbeat(); loop(); });
chrome.runtime.onInstalled.addListener(() => { armHeartbeat(); loop(); });
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
