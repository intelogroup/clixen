/**
 * The voice protocol, in one typed place.
 *
 * These shapes exist in three languages right now — Rust builds `SseEvent`
 * (`kind`/`text`/`data`/`state`), Python emits payload dicts and `VoiceStatus`,
 * and this renderer switches on the strings. Every rename across that boundary
 * fails silently: a misspelled `kind` falls through the switch and the orb just
 * stays quiet (that is exactly how the `kind` vs `type` bug shipped).
 *
 * New voice-facing code should import from here; `main.js` can adopt it
 * incrementally (`// @ts-check` at the top of the file turns the checks on).
 */

export type VoiceState = "idle" | "listening" | "hearing" | "thinking" | "speaking";

/** Every frame the backend can push over `/voice/events` or `/voice/process`. */
export type VoiceEventKind =
  | "state"
  | "partial"
  | "final"
  | "transcript"
  | "token"
  | "audio"
  | "done"
  | "error"
  | "interrupt"
  | "warning"
  | "cancel"
  | "ping";

export interface VoiceEvent {
  kind: VoiceEventKind;
  /** Caption text. Empty for `state`/`audio`. */
  text: string;
  /** base64 WAV bytes — present on `audio`. */
  data?: string;
  /** Present on `state`. */
  state?: VoiceState;
  /** Set by the agent for every frame of one turn. */
  turn_id?: string;
}

/** `GET /voice/session` and `POST /voice/listen`. */
export interface VoiceStatus {
  listening: boolean;
  state: VoiceState;
  chat_id: string;
  turns: number;
  last_rms: number;
  last_partial: string;
  last_final: string;
  mic: string | null;
  busy: boolean;
}

export interface VoiceListenResult extends VoiceStatus {
  ok: boolean;
}

/** The orb's backend commands that touch voice, fully typed. */
export interface OrbVoiceCommands {
  orb_listen: { args: { on?: boolean }; result: VoiceListenResult };
  orb_feed_audio: { args: { audioB64: string }; result: null };
  orb_events_start: { args: Record<string, never>; result: null };
  orb_cancel: { args: { chat_id?: string }; result: null };
}

export type OrbVoiceCommand = keyof OrbVoiceCommands;

/** Frames that carry caption text for the user. */
export function isCaptionKind(kind: VoiceEventKind): boolean {
  return kind === "partial" || kind === "final" || kind === "transcript" || kind === "token";
}

/** Maps a backend state to the orb's visual mode ("acting" is renderer-only). */
export function orbModeFor(state: VoiceState | undefined, micOn: boolean): "idle" | "listening" | "thinking" | "speaking" {
  switch (state) {
    case "hearing":
    case "listening":
      return micOn ? "listening" : "idle";
    case "thinking":
      return "thinking";
    case "speaking":
      return "speaking";
    default:
      return "idle";
  }
}
