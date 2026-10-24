import { listen, emit } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";

const line = document.getElementById("line");
const form = document.getElementById("ask");
const field = document.getElementById("ask-field");

let hideTimer = 0;

function render(text, kind) {
  line.textContent = text;
  line.classList.toggle("you", kind === "you");
  line.classList.add("show");
}

function fade(delay) {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    line.classList.remove("show");
  }, delay);
}

/* ---- typed input --------------------------------------------------------
 * The orb is a non-activating panel: it can never become key, so its webview
 * can't take keystrokes. This window is an ordinary window, so Rust can focus
 * it and hand the keyboard over when the user double-clicks the orb.
 * ------------------------------------------------------------------------ */

function enterInputMode() {
  line.classList.remove("show");
  form.classList.add("on");
  field.value = "";
  field.focus();
}

function leaveInputMode() {
  form.classList.remove("on");
  field.blur();
}

form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = field.value.trim();
  leaveInputMode();
  // Rust dismisses the strip and restores the app that had focus; the orb
  // picks the text up from this event.
  emit("orb-prompt", { text });
  invoke("orb_prompt_done").catch(() => {});
});

field.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    ev.preventDefault();
    leaveInputMode();
    emit("orb-prompt", { text: "" });
    invoke("orb_prompt_done").catch(() => {});
  }
});

// Rust owns positioning this window directly beneath the orb; we only ever
// decide what it says and when it dissolves.
listen("orb-caption", (ev) => {
  const { text, kind, persist, input } = ev.payload || {};

  if (input) {
    enterInputMode();
    return;
  }
  if (form.classList.contains("on")) return;

  if (!text) {
    // Clear the node as well as fading: leaving the old string behind makes the
    // DOM lie about what is on screen, which trips up anything reading it back
    // (tests, accessibility tools) and would flash a stale reply if the window
    // were ever shown again before the next render.
    line.textContent = "";
    fade(0);
    return;
  }
  render(text, kind);
  if (!persist) fade(kind === "you" ? 2400 : 3600);
});
