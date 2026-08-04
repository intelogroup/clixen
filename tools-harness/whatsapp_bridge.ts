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
import { promisify } from 'util';
import { logIncoming, logOutgoing } from './whatsapp_log.js';
import {
  initialState as _reconnectInitialState,
  reconnectDelay as _reconnectDelay,
  recordReconnectFailure as _recordReconnectFailure,
  resetReconnect as _resetReconnect,
  clearStuck as _clearStuck,
} from './whatsapp_reconnect_state.js';

const execAsync = promisify(exec);

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

const logger = pino({ level: 'debug' });

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
    browser: Browsers.macOS('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    keepAliveIntervalMs: 30_000,
    getMessage: async (key) => msgCache.get(key.id ?? '') ?? { conversation: '' },
  });

  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', handleConnectionUpdate);
  sock.ev.on('messages.upsert', handleMessagesUpsert);

  return sock;
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
    logger.info('QR code updated — scan with WhatsApp on your phone');
    if (pairingInProgress && pairingPhoneNumber && sock) {
      try {
        const code = await sock.requestPairingCode(pairingPhoneNumber);
        pairingCode = code;
        logger.info({ code }, 'Pairing code auto-refreshed');
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

function handleMessagesUpsert(
  { messages }: BaileysEventMap['messages.upsert'],
): void {
  for (const msg of messages) {
    const msgId = msg.key?.id;
    if (msgId && msg.message) msgCache.set(msgId, msg.message);

    if (msgId && sentIds.has(msgId)) continue;

    const remoteJid = msg.key.remoteJid ?? '';
    const messageText =
      msg.message?.conversation ??
      msg.message?.extendedTextMessage?.text ??
      msg.message?.buttonsResponseMessage?.selectedButtonId ??
      '';

    // Skip system/broadcast/newsletter JIDs — never reply to these
    if (
      !remoteJid ||
      remoteJid === 'status@broadcast' ||
      remoteJid.endsWith('@newsletter') ||
      remoteJid.endsWith('@broadcast')
    ) continue;

    // Persist to local archive (~/.clixen/whatsapp.db) for offline search.
    logIncoming(msg, messageText).catch(() => {});

    // Only process messages whose conversation JID is the user's own number (self-chat).
    // This unconditional guard covers BOTH directions:
    //   • fromMe=true  → user sent a msg to someone else (echoed by Baileys) — skip if remote ≠ own
    //   • fromMe=false → a contact replied to the user — skip (remote = contact JID ≠ own)
    // The bot only responds when the user messages their own number (self-chat).
    {
      const normalize = (jid: string) => jid.replace(/:\d+@/, '@');
      const myPn   = normalize(sock?.user?.id  ?? '');
      const myLid  = normalize((sock?.user as unknown as { lid?: string })?.lid ?? '');
      const remote = normalize(remoteJid);
      if (remote !== myPn && remote !== myLid) continue;
    }

    if (!messageText) continue;

    logger.info({ from: remoteJid, fromMe: msg.key.fromMe, text: messageText }, 'Incoming message');
    handleIncomingMessage(msg).catch(e =>
      logger.error({ error: (e as Error).message }, 'handleIncomingMessage threw'),
    );
  }
}

async function handleIncomingMessage(msg: proto.IWebMessageInfo): Promise<void> {
  const remoteJid = msg.key?.remoteJid!;
  const messageText =
    msg.message?.conversation ??
    msg.message?.extendedTextMessage?.text ??
    '';

  if (msg.key) {
    try { await sock!.readMessages([msg.key as Parameters<WASocket['readMessages']>[0][number]]); } catch (_) {}
  }
  try { await sock!.sendPresenceUpdate('composing', remoteJid); } catch (_) {}

  try {
    const response = await fetch(`${BOT_URL}/webhook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sender: canonicalJid(remoteJid),
        message: messageText,
        name: msg.pushName ?? remoteJid.split('@')[0],
      }),
    });

    if (!response.ok) throw new Error(`Bot responded with ${response.status}`);

    const replyData = await response.json() as { reply?: string; message?: string };
    const replyText = replyData.reply ?? replyData.message ?? '';

    if (replyText) {
      try { await sock!.sendPresenceUpdate('paused', remoteJid); } catch (_) {}

      if (isRateLimited(remoteJid)) {
        logger.warn({ jid: remoteJid }, 'Rate limit hit — dropping reply');
        return;
      }

      const charDelay = Math.min(replyText.length * 30, 3500);
      await jitter(800, Math.max(800, charDelay));

      // Retry transient send failures (e.g. a brief Baileys reconnect) — the
      // harness already did the expensive work by this point, so a blip here
      // shouldn't drop the reply the way an unretried send silently did before.
      let sent;
      for (let attempt = 0; ; attempt++) {
        try {
          sent = await sock!.sendMessage(remoteJid, { text: replyText });
          break;
        } catch (sendErr) {
          if (attempt >= 2) throw sendErr;
          await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
        }
      }
      if (sent?.key?.id) sentIds.add(sent.key.id);
      logOutgoing(remoteJid, replyText, sent?.key?.id).catch(() => {});
      logger.info({ to: remoteJid, length: replyText.length }, 'Sent reply');
    }
  } catch (error) {
    try { await sock!.sendPresenceUpdate('paused', remoteJid); } catch (_) {}
    logger.error({ error }, 'Failed to process message');
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
  if (connected) {
    return res.send(`<!DOCTYPE html><html><head><title>WhatsApp Connected</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp Connected</h1><p style="color:green">✓ Connected and ready.</p>
      </body></html>`);
  }
  if (!qrCode) {
    return res.send(`<!DOCTYPE html><html><head><title>WhatsApp QR</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp QR Code</h1><p>Waiting for QR code...</p>
      </body></html>`);
  }
  res.send(`<!DOCTYPE html><html><head><title>WhatsApp QR</title></head>
    <body style="font-family:system-ui;padding:40px;text-align:center">
      <h1>WhatsApp QR Code</h1>
      <p style="margin-bottom:20px">Scan this with WhatsApp on your phone</p>
      <img src="${qrCode}" alt="QR Code" style="max-width:300px;border:1px solid #ccc;border-radius:8px;padding:10px">
      <p style="margin-top:20px;color:#666">Or use <a href="/auth/pairing-code">pairing code</a></p>
    </body></html>`);
});

app.get('/api/qr-json', (_req: Request, res: Response) => {
  const stuck = stuckFlag();
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
      syncFullHistory: false,
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
    if (sock) {
      try { (sock as WASocket & { ws?: { close(): void } }).ws?.close(); } catch (_) {}
      sock = null;
    }
    connected = false;
    qrCode = null;
    pairingCode = null;
    res.json({ status: 'reset' });
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

app.get('/status', (_req: Request, res: Response) => {
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
  });
});

app.post('/send', async (req: Request, res: Response) => {
  if (!connected || !sock) {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }

  const { to, message } = req.body as { to?: string; message?: string };
  if (!to || !message) return res.status(400).json({ error: 'Missing to or message' });

  try {
    if (isRateLimited(to)) return res.status(429).json({ error: 'Rate limit exceeded' });
    await jitter(400, 1200);
    const sent = await sock.sendMessage(to, { text: message });
    if (sent?.key?.id) { msgCache.set(sent.key.id, { conversation: message }); sentIds.add(sent.key.id); }
    logOutgoing(to, message, sent?.key?.id).catch(() => {});
    res.json({ status: 'sent', to, message });
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
  await getSocket();
  await startHttpServer();
}

main().catch((error) => {
  console.error('BRIDGE FATAL:', String(error));
  console.error('Stack:', (error as Error)?.stack);
  logger.fatal({ error }, 'Failed to start bridge');
  process.exit(1);
});
