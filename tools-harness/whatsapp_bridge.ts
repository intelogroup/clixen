import express, { Request, Response } from 'express';
import { Boom } from '@hapi/boom';
import {
  makeWASocket,
  DisconnectReason,
  useMultiFileAuthState,
  Browsers,
  WASocket,
  proto,
  BaileysEventMap,
} from 'baileys';
import { fetchLatestWaWebVersion } from 'baileys/lib/Utils/generics.js';
import pino from 'pino';
import QRCode from 'qrcode';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { existsSync, mkdirSync } from 'fs';
import { createServer, Server } from 'http';
import { exec } from 'child_process';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { loadContacts, logContact, logIncoming, logOutgoing, logOwnerJids, fetchRecentHistory, findContactJid, archiveAvailable } from './whatsapp_log.js';
import {
  initialState as _reconnectInitialState,
  reconnectDelay as _reconnectDelay,
  recordReconnectFailure as _recordReconnectFailure,
  resetReconnect as _resetReconnect,
  clearStuck as _clearStuck,
} from './whatsapp_reconnect_state.js';

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

// ─── Anti-ban helpers ────────────────────────────────────────────────────────

function jitter(minMs = 800, maxMs = 3500): Promise<void> {
  return new Promise(r => setTimeout(r, minMs + Math.random() * (maxMs - minMs)));
}

const RATE_WINDOW_MS = 60_000;
const MAX_PER_WINDOW = 20;
const _rateCounts = new Map<string, { count: number; resetAt: number }>();

function isRateLimited(jid: string): boolean {
  const now = Date.now();
  const entry = _rateCounts.get(jid) ?? { count: 0, resetAt: now + RATE_WINDOW_MS };
  if (now > entry.resetAt) {
    entry.count = 0;
    entry.resetAt = now + RATE_WINDOW_MS;
  }
  entry.count++;
  _rateCounts.set(jid, entry);
  return entry.count > MAX_PER_WINDOW;
}

// A session rejected at the login handshake (e.g. 405 "Connection Failure")
// never recovers by retrying hard — WhatsApp treats persistent failures as
// needing a re-link. After a few consecutive failed reconnects we enter
// "stuck" mode: back off to a slow probe (10 min) instead of hammering, and
// surface a repair signal (QR or reset hint) so the operator can re-pair.
// The pure state machine lives in whatsapp_reconnect_state.ts (unit-testable
// without a live socket).
const _reconnectState = _reconnectInitialState();

function reconnectDelayMs(): number {
  return _reconnectDelay(_reconnectState);
}
function resetReconnect(): void {
  _resetReconnect(_reconnectState);
}
function recordReconnectFailureWrap(reasonText: string, errorMsg: string): boolean {
  const newlyStuck = _recordReconnectFailure(_reconnectState, reasonText, errorMsg);
  if (newlyStuck) {
    logger.error(
      { failures: _reconnectState.consecutiveFailures, reason: _reconnectState.stuckReason },
      'RECONNECT STORM — session likely needs re-pair (backing off to slow probe)',
    );
    // Best-effort fresh socket so Baileys can emit a QR while stuck. If the
    // old creds still block login, no QR arrives and repairAction stays
    // 'reset_session' — the UI/operator then hits /auth/reset for a clean re-pair.
    if (sock) {
      try { sock.end(undefined); } catch (_) {}
      sock = null;
    }
    setImmediate(() => getSocket().catch(e => logger.error({ error: (e as Error).message }, 'Stuck probe failed')));
  }
  return newlyStuck;
}
function clearStuckWrap(): void {
  _clearStuck(_reconnectState);
}
function stuckFlag(): boolean {
  return _reconnectState.stuck;
}
function stuckSinceValue(): number | null {
  return _reconnectState.stuckSince;
}
function stuckReasonValue(): string {
  return _reconnectState.stuckReason;
}

// ─── Config ──────────────────────────────────────────────────────────────────

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = parseInt(process.env.WHATSAPP_BRIDGE_PORT ?? '9235', 10);
const BOT_URL = process.env.WHATSAPP_BOT_URL ?? 'http://localhost:9236';
const SESSION_DIR = process.env.WHATSAPP_SESSION_DIR ?? join(__dirname, '.baileys_auth');

const logger = pino({
  level: process.env.WHATSAPP_LOG_LEVEL ?? 'info',
  // Baileys includes ephemeral/client pairing material in some debug records.
  // Keep the useful lifecycle metadata while preventing those values from
  // being persisted in launchd's messaging log.
  redact: [
    'helloMsg.clientHello.ephemeral',
    'node.devicePairingData',
  ],
});

if (!existsSync(SESSION_DIR)) mkdirSync(SESSION_DIR, { recursive: true });

// ─── State ───────────────────────────────────────────────────────────────────

const app = express();
app.use(express.json());

let sock: WASocket | null = null;
let qrCode: string | null = null;
let pairingCode: string | null = null;
let connected = false;
let pairingInProgress = false;
let pairingPhoneNumber: string | null = null;
let qrSequence = 0;
let qrIssuedAt: number | null = null;
type ContactRecord = {
  jid: string;
  lid?: string;
  name?: string;
  notify?: string;
  verifiedName?: string;
  lastSeenAt: number;
};
const contacts = new Map<string, ContactRecord>();
// Hello is sent at most once per process, only on a fresh pairing (isNewLogin)
// — never on a reconnect. The WhatsApp connection drops routinely (timedOut /
// badSession / network blips); greeting on every 'open' used to spam the
// owner's own thread with a hello per reconnect (~148 in 5 days).
let welcomeSent = false;

// Message cache so getMessage can answer retry requests ("waiting for message")
const msgCache = new Map<string, proto.IMessage>();
// IDs of messages we sent — used to skip echo in upsert handler
const sentIds = new Set<string>();

// ─── Crash guards ────────────────────────────────────────────────────────────

process.on('uncaughtException', (err) => logger.error({ err }, 'Uncaught exception'));
process.on('unhandledRejection', (reason) => logger.error({ reason }, 'Unhandled rejection'));

// ─── Socket lifecycle ────────────────────────────────────────────────────────

let waVersion: [number, number, number] | null = null;

async function resolveWaVersion(): Promise<[number, number, number]> {
  if (waVersion) return waVersion;
  try {
    const { version, isLatest } = await fetchLatestWaWebVersion();
    if (isLatest && version.length === 3) {
      waVersion = version as [number, number, number];
      logger.info({ version: waVersion.join('.') }, 'Using latest WhatsApp Web version');
    }
  } catch (error) {
    logger.warn({ error: (error as Error).message }, 'Failed to fetch latest WA version; falling back to bundled');
  }
  return waVersion ?? [2, 3000, 1044387223];
}

async function getSocket(): Promise<WASocket> {
  if (sock && connected) return sock;
  if (sock) {
    try { sock.end(undefined); } catch (_) {}
    sock = null;
  }

  const { state: authState, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

  sock = makeWASocket({
    version: await resolveWaVersion(),
    auth: authState,
    logger,
    // Keep the existing linked-session browser profile; switching an already
    // linked account to Desktop can trigger WhatsApp 428 termination.
    browser: Browsers.macOS('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: true,
    keepAliveIntervalMs: 30_000,
    getMessage: async (key) => msgCache.get(key.id ?? '') ?? { conversation: '' },
  });

  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', handleConnectionUpdate);
  sock.ev.on('contacts.upsert', handleContactsUpsert);
  sock.ev.on('messaging-history.set', handleHistorySet);
  sock.ev.on('messaging-history.status', (event) => {
    logger.info({
      tag: '[WA-HISTORY]',
      syncType: event.syncType,
      status: event.status,
      explicit: event.explicit,
    }, 'WhatsApp history sync status');
  });
  sock.ev.on('messages.upsert', handleMessagesUpsert);

  return sock;
}

function isUserContactJid(jid: string): boolean {
  return jid.endsWith('@s.whatsapp.net') || jid.endsWith('@lid');
}

function redactJid(jid: string): string {
  const [local, domain] = jid.split('@');
  if (!local || !domain) return '<unknown-jid>';
  return `${local.slice(0, 3)}…${local.slice(-2)}@${domain}`;
}

function upsertContact(record: ContactRecord): void {
  if (!isUserContactJid(record.jid)) return;
  const current = contacts.get(record.jid);
  contacts.set(record.jid, {
    ...current,
    ...record,
    lastSeenAt: Math.max(current?.lastSeenAt ?? 0, record.lastSeenAt),
  });
  logContact(record).catch(() => {});
}

function handleContactsUpsert(contactUpdates: BaileysEventMap['contacts.upsert']): void {
  for (const contact of contactUpdates) {
    const jid = contact.id ?? '';
    if (!jid) continue;
    upsertContact({
      jid,
      lid: contact.lid ?? undefined,
      name: contact.name ?? undefined,
      notify: contact.notify ?? undefined,
      verifiedName: contact.verifiedName ?? undefined,
      lastSeenAt: Date.now(),
    });
  }
  logger.info({ count: contactUpdates.length, totalContacts: contacts.size }, 'WhatsApp contacts updated');
}

function handleHistorySet(history: BaileysEventMap['messaging-history.set']): void {
  const { messages = [], contacts: historyContacts = [], isLatest, progress } = history;
  for (const contact of historyContacts) {
    const jid = contact.id ?? '';
    if (!jid) continue;
    upsertContact({
      jid,
      lid: contact.lid ?? undefined,
      name: contact.name ?? undefined,
      notify: contact.notify ?? undefined,
      verifiedName: contact.verifiedName ?? undefined,
      lastSeenAt: Date.now(),
    });
  }
  for (const msg of messages) {
    const msgId = msg.key?.id;
    if (msgId && msg.message) msgCache.set(msgId, msg.message);
    const text = extractMessageText(msg.message);
    if (text) logIncoming(msg, text).catch(() => {});
  }
  logger.info({
    tag: '[WA-HISTORY]',
    messageCount: messages.length,
    contactCount: historyContacts.length,
    isLatest,
    progress: progress ?? null,
  }, 'WhatsApp history batch received');
}

/**
 * Ask the linked phone for recent messages when a contact exists in the
 * directory but has not appeared in the local archive yet. Baileys delivers
 * the result asynchronously through messaging-history.set.
 */
async function fetchOnDemandHistory(jid: string, limit = 20): Promise<number> {
  if (!sock || !connected || !jid) return 0;
  try {
    await sock.fetchMessageHistory(
      Math.min(Math.max(limit, 1), 50),
      { remoteJid: jid, fromMe: false, id: '' },
      Date.now(),
    );
    logger.info({ tag: '[WA-HISTORY]', jid: redactJid(jid), limit }, 'Requested on-demand chat history');
  } catch (error) {
    logger.warn({ tag: '[WA-HISTORY]', jid: redactJid(jid), error: (error as Error).message }, 'On-demand history request failed');
    return 0;
  }

  // The history event is asynchronous. Poll the archive briefly rather than
  // racing the event handler and sending an empty context to the AI.
  for (let attempt = 0; attempt < 20; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 250));
    const history = await fetchRecentHistory(jid, limit);
    if (history.length) {
      logger.info({ tag: '[WA-HISTORY]', jid: redactJid(jid), historyCount: history.length }, 'On-demand chat history archived');
      return history.length;
    }
  }
  logger.warn({ tag: '[WA-HISTORY]', jid: redactJid(jid) }, 'On-demand history returned no archive messages');
  return 0;
}

async function handleConnectionUpdate(
  update: BaileysEventMap['connection.update'],
): Promise<void> {
  const { connection, lastDisconnect, qr, isNewLogin } = update;
  const registered = (sock?.authState?.creds as { registered?: boolean })?.registered;

  logger.info({
    connection,
    qr: qr ? 'yes' : 'no',
    isNewLogin: isNewLogin ?? null,
    registered: registered ?? null,
  }, 'Connection update');

  if (qr) {
    qrCode = await QRCode.toDataURL(qr);
    qrSequence += 1;
    qrIssuedAt = Date.now();
    logger.info({ qrSequence, qrLength: qr.length }, 'QR code updated — scan with WhatsApp on your phone');
    if (pairingInProgress && pairingPhoneNumber && sock) {
      try {
        const code = await sock.requestPairingCode(pairingPhoneNumber);
        pairingCode = code;
        logger.info({ phoneDigits: pairingPhoneNumber.length, pairingCodeLength: code.length }, 'Pairing code auto-refreshed');
      } catch (e) {
        logger.error({ error: (e as Error).message }, 'Auto-refresh pairing code failed');
      }
    }
  }

  if (connection === 'close') {
    connected = false;
    const disconnectError = lastDisconnect?.error;
    const reason = new Boom(disconnectError)?.output?.statusCode;
    const reasonText = (DisconnectReason as Record<number, string>)[reason] ?? 'unknown';

    logger.error({
      reason,
      reasonText,
      qrSequence,
      qrAgeMs: qrIssuedAt === null ? null : Date.now() - qrIssuedAt,
      pairingInProgress,
      sessionRegistered: registered ?? null,
      disconnectError: disconnectError
        ? {
            message: (disconnectError as Error)?.message ?? String(disconnectError),
            stack: (disconnectError as Error)?.stack?.split('\n').slice(0, 4),
            output: (disconnectError as Boom)?.output ?? null,
          }
        : null,
    }, 'Connection closed — debugging disconnect');

    const shouldReconnect = reason !== DisconnectReason.loggedOut;

    const isRestartRequired = reason === 515;
    if (isRestartRequired) pairingInProgress = false;

    if (shouldReconnect && (!pairingInProgress || isRestartRequired)) {
      if (!stuckFlag()) {
        recordReconnectFailureWrap(
          reasonText,
          (disconnectError as Error)?.message ?? String(disconnectError ?? 'unknown'),
        );
      }
      const delay = reconnectDelayMs();
      logger.info({ delay, stuck: stuckFlag() }, `Reconnecting in ${delay}ms...`);
      sock = null;
      setTimeout(() => getSocket().catch(e => logger.error({ error: (e as Error).message }, 'Reconnect failed')), delay);
    } else if (!shouldReconnect) {
      logger.warn('Logged out — clear .baileys_auth/ and restart to re-pair');
      sock = null;
    }
  } else if (connection === 'open') {
    connected = true;
    pairingInProgress = false;
    pairingPhoneNumber = null;
    resetReconnect();
    if (stuckFlag()) {
      clearStuckWrap();
      logger.info('Reconnect storm resolved — session is healthy again');
    }
    logger.info('Connected to WhatsApp!');
    qrCode = null;
    pairingCode = null;

    if (sock?.user?.id) {
      const myJid = sock.user.id;
      const myLid = (sock.user as unknown as { lid?: string }).lid ?? '';
      logOwnerJids([myJid, myLid]).catch(() => {});
      if (isNewLogin && !welcomeSent) {
        welcomeSent = true;
        logger.info({ jid: myJid }, 'Sending hello to own number');
        try {
          const helloSent = await sock.sendMessage(myJid, {
            text: '👋 Hello! WhatsApp bridge connected successfully. You can now chat with G4L from WhatsApp!',
          });
          if (helloSent?.key?.id) sentIds.add(helloSent.key.id);
          logger.info('Hello message sent');
        } catch (e) {
          logger.error({ error: (e as Error).message }, 'Failed to send hello');
        }
      } else {
        logger.info({ jid: myJid, isNewLogin }, 'Connected — skipping hello (reconnect or already greeted)');
      }
    }
  }
}

async function handleMessagesUpsert(
  { messages }: BaileysEventMap['messages.upsert'],
): Promise<void> {
  for (const msg of messages) {
    const msgId = msg.key?.id;
    if (msgId && msg.message) msgCache.set(msgId, msg.message);

    if (msgId && sentIds.has(msgId)) continue;

    const remoteJid = msg.key.remoteJid ?? '';
    if (msg.pushName && isUserContactJid(remoteJid)) {
      upsertContact({ jid: remoteJid, name: msg.pushName, lastSeenAt: Date.now() });
    }
    const messageText = extractMessageText(msg.message);

    // Skip system/broadcast/newsletter JIDs — never reply to these
    if (
      !remoteJid ||
      remoteJid === 'status@broadcast' ||
      remoteJid.endsWith('@newsletter') ||
      remoteJid.endsWith('@broadcast')
    ) continue;

    // Persist to local archive (~/.clixen/whatsapp.db) for offline search.
    logIncoming(msg, messageText).catch(() => {});

    if (!messageText) continue;

    const normalize = (jid: string) => jid.replace(/:\d+@/, '@');
    const myPn   = normalize(sock?.user?.id  ?? '');
    const myLid  = normalize((sock?.user as unknown as { lid?: string })?.lid ?? '');
    const remote = normalize(remoteJid);
    const isSelfChat = remote === myPn || remote === myLid;

    // @ai/@clixen mid-conversation: fires ONLY on the owner's own outgoing text
    // (fromMe=true) in ANY chat — never on something the other party sent, so
    // there's no remote-command-injection surface. The reply never goes back
    // into this chat (the other party would see it); it's routed to self-chat.
    const isAiTrigger = msg.key.fromMe === true && !isSelfChat && AI_TRIGGER_RE.test(messageText);
    const isPrivateAiTrigger = msg.key.fromMe === true && isSelfChat && AI_TRIGGER_RE.test(messageText);
    let namedContactTrigger: { contactQuery: string; question: string; jid: string } | null = null;
    if (msg.key.fromMe === true && isSelfChat && !isPrivateAiTrigger) {
      const parsed = parseNamedContactTrigger(messageText);
      if (parsed) {
        const jid = await findContactJid(contactLookupQuery(parsed.contactQuery));
        if (jid) namedContactTrigger = { ...parsed, jid };
      }
    }
    const hasPrivateTrigger = isPrivateAiTrigger || Boolean(namedContactTrigger);

    // Otherwise: only process messages whose conversation JID is the user's own
    // number (self-chat). Covers both directions — fromMe=true echoed to self,
    // or a contact replying in self-chat (shouldn't happen, but skip if not own).
    if (!isSelfChat && !isAiTrigger) continue;

    logger.info({
      from: redactJid(remoteJid),
      fromMe: msg.key.fromMe,
      textLength: messageText.length,
      isAiTrigger,
      isPrivateAiTrigger: hasPrivateTrigger,
      namedContact: namedContactTrigger?.contactQuery ?? null,
    }, 'Incoming message');
    handleIncomingMessage(msg, {
      directChatTrigger: isAiTrigger,
      privateChatTrigger: hasPrivateTrigger,
      privateContactQuery: namedContactTrigger?.contactQuery,
      privateContactJid: namedContactTrigger?.jid,
      privateQuestion: namedContactTrigger?.question,
    }).catch(e =>
      logger.error({ error: (e as Error).message }, 'handleIncomingMessage threw'),
    );
  }
}

function extractMessageText(message: proto.IMessage | null | undefined): string {
  return message?.conversation ??
    message?.extendedTextMessage?.text ??
    message?.buttonsResponseMessage?.selectedButtonId ??
    message?.listResponseMessage?.title ??
    '';
}

function quotedMessageText(msg: proto.IWebMessageInfo): string {
  const quoted = msg.message?.extendedTextMessage?.contextInfo?.quotedMessage;
  return extractMessageText(quoted);
}

async function handleIncomingMessage(
  msg: proto.IWebMessageInfo,
  options: {
    directChatTrigger?: boolean;
    privateChatTrigger?: boolean;
    privateContactQuery?: string;
    privateContactJid?: string;
    privateQuestion?: string;
  } = {},
): Promise<void> {
  const isAiTrigger = options.directChatTrigger === true;
  const isPrivateAiTrigger = options.privateChatTrigger === true;
  const remoteJid = msg.key?.remoteJid!;
  let stage = 'start';
  const messageText =
    extractMessageText(msg.message);
  const quotedText = quotedMessageText(msg);

  // AI-trigger fires from inside a real contact chat, but the reply is never
  // sent back into that chat — it always goes to self-chat, so the other
  // party never sees the answer (or that one exists).
  const targetJid = isAiTrigger || isPrivateAiTrigger ? selfJid() : remoteJid;

  if (msg.key) {
    try { await sock!.readMessages([msg.key as Parameters<WASocket['readMessages']>[0][number]]); } catch (_) {}
  }
  try { await sock!.sendPresenceUpdate('composing', targetJid); } catch (_) {}

  try {
    let payload: Record<string, unknown> = {
      sender: canonicalJid(remoteJid),
      message: messageText,
      name: msg.pushName ?? remoteJid.split('@')[0],
    };

    if (isPrivateAiTrigger) {
      stage = 'parse-private-command';
      const privateRequest = messageText.replace(AI_TRIGGER_RE, '').trim();
      const match = options.privateContactQuery && options.privateQuestion
        ? [, options.privateContactQuery, options.privateQuestion]
        : privateRequest.match(/^(.+?)\s*(?::|—|–)\s*(.+)$/);
      if (!match) {
        logger.warn({ tag: '[WA-PRIVATE]', stage, textLength: messageText.length }, 'Invalid private @ai format');
        await sock!.sendMessage(targetJid, {
          text: 'Format privé : @ai Nom du contact: ta demande\nExemple : @ai Solini: résume cette conversation',
        });
        return;
      }
      const [, contactQuery, question] = match;
      stage = 'resolve-contact';
      const contactJid = options.privateContactJid
        ?? await findContactJid(contactQuery)
        ?? await resolveMacContactJid(contactQuery);
      logger.info({
        tag: '[WA-PRIVATE]',
        stage,
        queryLength: contactQuery.trim().length,
        resolved: Boolean(contactJid),
      }, 'Private @ai contact resolution');
      if (!contactJid) {
        logger.warn({ tag: '[WA-PRIVATE]', stage, queryLength: contactQuery.trim().length }, 'Private @ai contact not found');
        await sock!.sendMessage(targetJid, {
          text: `Je ne trouve pas le contact « ${contactQuery.trim()} » dans l’historique WhatsApp.`,
        });
        return;
      }
      stage = 'load-history';
      let history = await fetchRecentHistory(contactJid, 20);
      if (!history.length) {
        stage = 'fetch-history';
        await fetchOnDemandHistory(contactJid, 20);
        history = await fetchRecentHistory(contactJid, 20);
      }
      logger.info({ tag: '[WA-PRIVATE]', stage, historyCount: history.length }, 'Private @ai history loaded');
      const contextText = history
        .map(h => `[${h.from_me ? 'A' : 'B'}] ${h.text}`)
        .join('\n');
      payload = {
        sender: canonicalJid(selfJid()),
        source_chat: canonicalJid(contactJid),
        message: question.trim(),
        context: [quotedText ? `[Quoted message] ${quotedText}` : '', contextText].filter(Boolean).join('\n'),
        source_name: contactQuery.trim(),
        fresh_context: true,
      };
    } else if (isAiTrigger) {
      const question = messageText.replace(AI_TRIGGER_RE, '').trim();
      let history = await fetchRecentHistory(remoteJid, 20);
      if (!history.length) {
        await fetchOnDemandHistory(remoteJid, 20);
        history = await fetchRecentHistory(remoteJid, 20);
      }
      const sourceName = msg.pushName ?? remoteJid.split('@')[0];
      // Neutral [A]/[B] tags, not real names — labeling turns from real
      // USER:/ASSISTANT: makes the summarizer hallucinate a chat reply
      // instead of treating this as inert context (same bug as conversation fold).
      const contextText = history
        .map(h => `[${h.from_me ? 'A' : 'B'}] ${h.text}`)
        .join('\n');

      payload = {
        sender: canonicalJid(selfJid()),
        source_chat: canonicalJid(remoteJid),
        message: question || 'Summarize this conversation.',
        context: [quotedText ? `[Quoted message] ${quotedText}` : '', contextText].filter(Boolean).join('\n'),
        source_name: sourceName,
        fresh_context: true,
      };
    }

    const response = await fetch(`${BOT_URL}/webhook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    stage = 'bot-webhook';
    if (!response.ok) {
      logger.error({ tag: '[WA-PRIVATE]', stage, status: response.status }, 'AI bot webhook failed');
      throw new Error(`Bot responded with ${response.status}`);
    }

    const replyData = await response.json() as { reply?: string; message?: string };
    const replyText = replyData.reply ?? replyData.message ?? '';

    if (replyText) {
      try { await sock!.sendPresenceUpdate('paused', targetJid); } catch (_) {}

      if (isRateLimited(targetJid)) {
        logger.warn({ jid: targetJid }, 'Rate limit hit — dropping reply');
        return;
      }

      const charDelay = Math.min(replyText.length * 30, 3500);
      await jitter(800, Math.max(800, charDelay));

      // Retry transient send failures (e.g. a brief Baileys reconnect) — the
      // harness already did the expensive work by this point, so a blip here
      // shouldn't drop the reply the way an unretried send silently did before.
      let sent;
      stage = 'send-private-reply';
      for (let attempt = 0; ; attempt++) {
        try {
          sent = await sock!.sendMessage(targetJid, { text: replyText });
          break;
        } catch (sendErr) {
          logger.warn({ tag: '[WA-PRIVATE]', stage, attempt: attempt + 1, error: (sendErr as Error).message }, 'WhatsApp reply send failed');
          if (attempt >= 2) throw sendErr;
          await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
        }
      }
      if (sent?.key?.id) sentIds.add(sent.key.id);
      logOutgoing(targetJid, replyText, sent?.key?.id).catch(() => {});
      logger.info({
        tag: '[WA-PRIVATE]',
        to: redactJid(targetJid),
        length: replyText.length,
        isAiTrigger,
        isPrivateAiTrigger,
      }, 'Sent reply');
    }
  } catch (error) {
    try { await sock!.sendPresenceUpdate('paused', targetJid); } catch (_) {}
    logger.error({ tag: '[WA-PRIVATE]', stage, error: (error as Error).message }, 'Failed to process message');
  }
}

// ─── Chat identity canonicalization ─────────────────────────────────────────

// WhatsApp introduced LID (1934...@lid) alongside the phone-number JID
// (1857...@s.whatsapp.net) for the same user. The bridge only services the
// owner's own number (self-chat), so every forwarded message is the owner —
// but the JID form can flip between PN and LID across reconnects, which used
// to fork the conversation into two session files (whatsapp_...@lid vs
// whatsapp_...@s.whatsapp.net). Canonicalize everything to the phone-number
// JID so one user = one chat_id = one session file.
// Matches "@ai" / "@clixen" at the start of a message, case-insensitive.
const AI_TRIGGER_RE = /^@(ai|clixen)\b[\s,:-]*/i;
const NAMED_CONTACT_TRIGGER_RE = /@([^\s:—–]+)/i;
const CONTACT_ALIASES: Record<string, string> = { synsia: 'synsiou' };

function contactLookupQuery(query: string): string {
  return CONTACT_ALIASES[query.trim().toLocaleLowerCase()] ?? query.trim();
}

function parseNamedContactTrigger(text: string): { contactQuery: string; question: string } | null {
  if (AI_TRIGGER_RE.test(text)) return null;
  const match = text.match(NAMED_CONTACT_TRIGGER_RE);
  if (!match) return null;
  const question = [
    text.slice(0, match.index).trim(),
    text.slice((match.index ?? 0) + match[0].length).replace(/^[\s:—–]+/, '').trim(),
  ].filter(Boolean).join(' ');
  return { contactQuery: match[1].trim(), question: question || 'Summarize this conversation.' };
}

/**
 * WhatsApp often reconnects without sending a contacts.upsert event. In that
 * case the local WhatsApp contacts table has no display names even though the
 * same person exists in the Mac address book. Resolve the name there, verify
 * the phone number with WhatsApp, and persist the resulting JID for future
 * private @ai commands.
 */
async function resolveMacContactJid(query: string): Promise<string | null> {
  if (!sock || !query.trim()) return null;
  const escaped = query.trim().replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const script = `tell application "Contacts"
    launch
    delay 1
    set out to ""
    set matches to every person whose name contains "${escaped}"
    repeat with p in matches
      repeat with ph in phones of p
        set out to out & (value of ph) & "\\n"
      end repeat
    end repeat
    return out
  end tell`;
  try {
    const result = await execFileAsync('/usr/bin/osascript', ['-e', script], { timeout: 20_000 });
    const numbers = String(result.stdout)
      .split(/\r?\n/)
      .map(value => value.replace(/\D/g, ''))
      .filter(value => value.length >= 7);
    if (!numbers.length) return null;
    const matches = (await sock.onWhatsApp(...numbers)) ?? [];
    const match = matches.find(item => item.exists && item.jid);
    if (!match?.jid) return null;
    upsertContact({ jid: match.jid, name: query.trim(), lastSeenAt: Date.now() });
    return match.jid;
  } catch (error) {
    const errorCode = (error as NodeJS.ErrnoException).code ?? 'CONTACTS_LOOKUP_FAILED';
    logger.warn({
      tag: '[WA-PRIVATE]',
      queryLength: query.trim().length,
      errorCode,
    }, 'Mac contact resolution failed');
    return null;
  }
}

function selfJid(): string {
  const normalize = (j: string) => j.replace(/:\d+@/, '@');
  return normalize(sock?.user?.id ?? '');
}

function canonicalJid(jid: string): string {
  const normalize = (j: string) => j.replace(/:\d+@/, '@');
  const myPn = normalize(sock?.user?.id ?? '');
  const myLid = normalize((sock?.user as unknown as { lid?: string })?.lid ?? '');
  const remote = normalize(jid);
  if (remote === myLid && myPn) return myPn;
  return remote;
}

// ─── HTTP routes ─────────────────────────────────────────────────────────────

app.get('/auth/qr', (_req: Request, res: Response) => {
  logger.info({ connected, qrSequence, qrAgeMs: qrIssuedAt === null ? null : Date.now() - qrIssuedAt }, 'QR page requested');
  if (connected) {
    return res.send(`<!DOCTYPE html><html><head><title>WhatsApp Connected</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp Connected</h1><p style="color:green">✓ Connected and ready.</p>
      </body></html>`);
  }
  res.send(`<!DOCTYPE html><html><head><title>WhatsApp QR</title></head>
    <body style="font-family:system-ui;padding:40px;text-align:center">
      <h1>WhatsApp QR Code</h1>
      <p id="status" style="margin-bottom:20px">Waiting for QR code...</p>
      <img id="qr" src="${qrCode ?? ''}" alt="QR Code" style="max-width:300px;border:1px solid #ccc;border-radius:8px;padding:10px">
      <p id="age" style="color:#666"></p>
      <p style="margin-top:20px;color:#666">Or use <a href="/auth/pairing-code">pairing code</a></p>
      <script>
        const qr = document.getElementById('qr');
        const status = document.getElementById('status');
        const age = document.getElementById('age');
        async function refreshQr() {
          try {
            const response = await fetch('/api/qr-json', { cache: 'no-store' });
            const data = await response.json();
            if (data.connected) {
              status.textContent = 'Connected — you can close this page.';
              age.textContent = '';
              return;
            }
            if (data.qr) {
              qr.src = data.qr;
              status.textContent = 'Scan this QR code with WhatsApp on your phone';
              age.textContent = 'QR refreshed automatically; age: ' + Math.round((data.qrAgeMs || 0) / 1000) + 's';
            } else {
              status.textContent = 'Waiting for a fresh QR code...';
            }
          } catch (error) {
            status.textContent = 'Bridge unavailable — retrying...';
          }
        }
        refreshQr();
        setInterval(refreshQr, 2000);
      </script>
    </body></html>`);
});

app.get('/api/qr-json', (_req: Request, res: Response) => {
  const stuck = stuckFlag();
  logger.debug({ connected, qrAvailable: !!qrCode, qrSequence, qrAgeMs: qrIssuedAt === null ? null : Date.now() - qrIssuedAt, stuck }, 'QR status requested');
  res.json({
    status: connected ? 'connected' : 'pending',
    qr: qrCode,
    connected,
    stuck,
    stuckSince: stuckSinceValue(),
    stuckReason: stuckReasonValue(),
    repairNeeded: stuck,
    repairAction: stuck && !qrCode ? 'reset_session' : (stuck ? 'scan_qr' : undefined),
  });
});

app.post('/auth/pairing-code', async (req: Request, res: Response) => {
  const phoneNumber = (req.body.phoneNumber as string) || (req.query.phoneNumber as string) || '';
  if (!phoneNumber) return res.status(400).json({ error: 'phoneNumber required' });
  if (connected) return res.json({ status: 'already_connected' });

  pairingInProgress = true;
  pairingPhoneNumber = phoneNumber;
  try {
    if (sock) {
      try { sock.end(undefined); } catch (_) {}
      sock = null;
      connected = false;
    }

    const { state: authState, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    sock = makeWASocket({
      version: await resolveWaVersion(),
      logger,
    auth: authState,
      browser: Browsers.macOS('Chrome'),
      printQRInTerminal: false,
      markOnlineOnConnect: false,
    syncFullHistory: true,
      keepAliveIntervalMs: 30_000,
      getMessage: async (key) => msgCache.get(key.id ?? '') ?? { conversation: '' },
    });

    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', handleConnectionUpdate);
    sock.ev.on('messages.upsert', handleMessagesUpsert);

    await new Promise(r => setTimeout(r, 2000));
    const code = await sock.requestPairingCode(phoneNumber);
    pairingCode = code;
    logger.info({ code }, 'Pairing code generated');
    res.json({ code });
  } catch (error) {
    pairingInProgress = false;
    pairingPhoneNumber = null;
    logger.error({ error: (error as Error).message }, 'Pairing code error');
    res.status(500).json({ error: (error as Error).message });
  }
});

app.get('/auth/pairing-code', (_req: Request, res: Response) => {
  if (connected) {
    return res.send(`<!DOCTYPE html><html><head><title>WhatsApp Connected</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp Connected</h1><p style="color:green">✓ Connected and ready.</p>
      </body></html>`);
  }

  if (pairingCode) {
    const formatted = pairingCode.length === 8
      ? `${pairingCode.slice(0, 4)}-${pairingCode.slice(4)}`
      : pairingCode;
    return res.send(`<!DOCTYPE html><html><head><title>WhatsApp Pairing Code</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp Pairing Code</h1>
        <p style="margin-bottom:20px">Enter this code in WhatsApp on your phone</p>
        <div style="font-size:52px;font-weight:bold;letter-spacing:6px;padding:20px 32px;border:2px solid #25D366;border-radius:12px;display:inline-block;background:#f6fff9;color:#111;">${formatted}</div>
        <p style="margin-top:20px;color:#555">In WhatsApp: <strong>Settings → Linked Devices → Link a Device → Link with phone number</strong></p>
        <p style="margin-top:12px"><a href="/auth/qr">← Use QR code instead</a></p>
      </body></html>`);
  }

  const phone = (process.env.WHATSAPP_DEFAULT_TARGET ?? '').replace(/^\+/, '');
  res.send(`<!DOCTYPE html><html><head><title>WhatsApp Pairing Code</title>
    <script>
      async function requestCode() {
        const phone = document.getElementById('phone').value.replace(/[^0-9]/g,'');
        const btn = document.getElementById('btn');
        btn.disabled = true; btn.textContent = 'Requesting…';
        try {
          const r = await fetch('/auth/pairing-code', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phoneNumber:phone})});
          const d = await r.json();
          if (d.code) {
            const fmt = d.code.length===8 ? d.code.slice(0,4)+'-'+d.code.slice(4) : d.code;
            document.getElementById('result').innerHTML =
              '<div style="font-size:52px;font-weight:bold;letter-spacing:6px;padding:20px 32px;border:2px solid #25D366;border-radius:12px;display:inline-block;background:#f6fff9;color:#111;">'+fmt+'</div>'+
              '<p style="margin-top:16px;color:#555">In WhatsApp: <strong>Settings → Linked Devices → Link a Device → Link with phone number</strong></p>';
          } else {
            document.getElementById('result').innerHTML='<p style="color:red">'+(d.error||JSON.stringify(d))+'</p>';
            btn.disabled=false; btn.textContent='Get Pairing Code';
          }
        } catch(e) {
          document.getElementById('result').innerHTML='<p style="color:red">'+e.message+'</p>';
          btn.disabled=false; btn.textContent='Get Pairing Code';
        }
      }
    </script></head>
    <body style="font-family:system-ui;padding:40px;text-align:center">
      <h1>Link WhatsApp</h1>
      <p style="color:#555">Enter your phone number (with country code, no +)</p>
      <input id="phone" type="tel" value="${phone}" placeholder="18574261739"
        style="font-size:20px;padding:10px 16px;border:1px solid #ccc;border-radius:8px;width:220px;text-align:center;margin-bottom:16px"><br>
      <button id="btn" onclick="requestCode()"
        style="font-size:18px;padding:12px 28px;background:#25D366;color:white;border:none;border-radius:8px;cursor:pointer">
        Get Pairing Code
      </button>
      <div id="result" style="margin-top:28px"></div>
      <p style="margin-top:32px"><a href="/auth/qr">← Use QR code instead</a></p>
    </body></html>`);
});

app.post('/auth/reset', (_req: Request, res: Response) => {
  try {
    logger.warn({ connected, qrSequence, sessionDir: SESSION_DIR }, 'Authentication session reset requested');
    if (sock) {
      try { (sock as WASocket & { ws?: { close(): void } }).ws?.close(); } catch (_) {}
      sock = null;
    }
    connected = false;
    qrCode = null;
    qrIssuedAt = null;
    pairingCode = null;
    res.json({ status: 'reset' });
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

app.get('/status', async (_req: Request, res: Response) => {
  const stuck = stuckFlag();
  res.json({
    status: connected ? 'connected' : 'disconnected',
    botUrl: BOT_URL,
    qrAvailable: !!qrCode,
    sessionExists: existsSync(join(SESSION_DIR, 'creds.json')),
    stuck,
    stuckSince: stuckSinceValue(),
    stuckReason: stuckReasonValue(),
    repairNeeded: stuck,
    contactCount: contacts.size,
    // false = message archive / @ai context lookup / contact resolution are all
    // silently no-op'ing, almost always a better-sqlite3 native-module ABI
    // mismatch (npm rebuild better-sqlite3 fixes it) — see messaging_stderr.log.
    archiveAvailable: await archiveAvailable(),
  });
});

app.get('/contacts', (_req: Request, res: Response) => {
  const ownJids = new Set([
    canonicalJid(sock?.user?.id ?? ''),
    canonicalJid((sock?.user as unknown as { lid?: string })?.lid ?? ''),
  ].filter(Boolean));
  const result = [...contacts.values()]
    .filter(contact => !ownJids.has(canonicalJid(contact.jid)))
    .sort((a, b) => (a.name ?? a.notify ?? a.jid).localeCompare(b.name ?? b.notify ?? b.jid))
    .map(contact => ({
      jid: contact.jid,
      lid: contact.lid,
      name: contact.name ?? contact.notify ?? contact.verifiedName ?? contact.jid.split('@')[0],
      verifiedName: contact.verifiedName,
      lastSeenAt: contact.lastSeenAt,
    }));
  logger.info({ count: result.length }, 'WhatsApp contacts requested');
  res.json({ status: connected ? 'connected' : 'disconnected', contacts: result });
});

app.post('/send', async (req: Request, res: Response) => {
  if (!connected || !sock) {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }

  const { to, message } = req.body as { to?: string; message?: string };
  if (!to || !message) return res.status(400).json({ error: 'Missing to or message' });

  try {
    const recipientJid = to.includes('@') ? to : `${to}@s.whatsapp.net`;
    if (isRateLimited(recipientJid)) return res.status(429).json({ error: 'Rate limit exceeded' });
    upsertContact({ jid: recipientJid, lastSeenAt: Date.now() });
    await jitter(400, 1200);
    const sent = await sock.sendMessage(recipientJid, { text: message });
    if (sent?.key?.id) { msgCache.set(sent.key.id, { conversation: message }); sentIds.add(sent.key.id); }
    logOutgoing(recipientJid, message, sent?.key?.id).catch(() => {});
    res.json({ status: 'sent', to: recipientJid, message });
  } catch (error) {
    logger.error({ error }, 'Failed to send message');
    res.status(500).json({ error: (error as Error).message });
  }
});

// ─── HTTP server with EADDRINUSE recovery ────────────────────────────────────

async function startHttpServer(): Promise<void> {
  return new Promise((resolve, reject) => {
    const server: Server = createServer(app);

    server.on('error', async (err: NodeJS.ErrnoException) => {
      if (err.code === 'EADDRINUSE') {
        logger.warn({ port: PORT }, 'Port in use — killing old process and retrying');
        try {
          await execAsync(`lsof -ti TCP:${PORT} | xargs kill -9 2>/dev/null || true`);
          await new Promise(r => setTimeout(r, 1000));
          server.listen(PORT, () => {
            logger.info({ port: PORT, botUrl: BOT_URL }, 'WhatsApp bridge listening');
            resolve();
          });
        } catch (e) {
          reject(e);
        }
      } else {
        reject(err);
      }
    });

    server.listen(PORT, () => {
      logger.info({ port: PORT, botUrl: BOT_URL }, 'WhatsApp bridge listening');
      resolve();
    });
  });
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  try {
    const savedContacts = await loadContacts();
    for (const contact of savedContacts) upsertContact({
      jid: contact.jid,
      lid: contact.lid ?? undefined,
      name: contact.name ?? undefined,
      notify: contact.notify ?? undefined,
      verifiedName: contact.verifiedName ?? undefined,
      lastSeenAt: contact.lastSeenAt,
    });
    logger.info({ count: contacts.size }, 'Loaded persisted WhatsApp contacts from SQLite');
  } catch (error) {
    logger.warn({ error: (error as Error).message }, 'Failed to load persisted WhatsApp contacts');
  }
  await getSocket();
  await startHttpServer();
}

main().catch((error) => {
  console.error('BRIDGE FATAL:', String(error));
  console.error('Stack:', (error as Error)?.stack);
  logger.fatal({ error }, 'Failed to start bridge');
  process.exit(1);
});
