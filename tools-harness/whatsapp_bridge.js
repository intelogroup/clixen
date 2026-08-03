import express from 'express';
import { Boom } from '@hapi/boom';
import makeWaSocket, { DisconnectReason, useMultiFileAuthState, fetchLatestBaileysVersion } from 'baileys';
import pino from 'pino';
import QRCode from 'qrcode';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { existsSync, mkdirSync } from 'fs';
import { logIncoming, logOutgoing } from './whatsapp_log.js';

// ─── Anti-ban helpers ────────────────────────────────────────────────────────

// Random delay between min–max ms
function jitter(minMs = 800, maxMs = 3500) {
  return new Promise(r => setTimeout(r, minMs + Math.random() * (maxMs - minMs)));
}

// Per-JID rate limiter: max MAX_PER_WINDOW messages per WINDOW_MS
const RATE_WINDOW_MS  = 60_000;   // 1 minute
const MAX_PER_WINDOW  = 20;       // max outgoing messages per JID per minute
const _rateCounts     = new Map(); // jid → { count, resetAt }

function isRateLimited(jid) {
  const now = Date.now();
  const entry = _rateCounts.get(jid) || { count: 0, resetAt: now + RATE_WINDOW_MS };
  if (now > entry.resetAt) {
    entry.count = 0;
    entry.resetAt = now + RATE_WINDOW_MS;
  }
  entry.count++;
  _rateCounts.set(jid, entry);
  return entry.count > MAX_PER_WINDOW;
}

// Exponential backoff reconnect delay (3s, 6s, 12s … cap 120s)
let _reconnectAttempts = 0;
function reconnectDelay() {
  const delay = Math.min(3000 * Math.pow(2, _reconnectAttempts), 120_000);
  _reconnectAttempts++;
  return delay;
}
function resetReconnect() { _reconnectAttempts = 0; }

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = process.env.WHATSAPP_BRIDGE_PORT || 9235;
const BOT_URL = process.env.WHATSAPP_BOT_URL || 'http://localhost:9236';
const SESSION_DIR = process.env.WHATSAPP_SESSION_DIR || join(__dirname, '.baileys_auth');

const logger = pino({ level: 'info' });

if (!existsSync(SESSION_DIR)) {
  mkdirSync(SESSION_DIR, { recursive: true });
}

const app = express();
app.use(express.json());

let sock = null;
let qrCode = null;
let pairingCode = null;
let connected = false;
let pairingInProgress = false;
let pairingPhoneNumber = null;

// Message cache so getMessage can answer retry requests (fixes "waiting for message")
const msgCache = new Map();
// IDs of messages we sent — used to skip echo in upsert handler
const sentIds = new Set();

// Prevent unhandled rejections from crashing the process
process.on('uncaughtException', (err) => logger.error({ err }, 'Uncaught exception'));
process.on('unhandledRejection', (reason) => logger.error({ reason }, 'Unhandled rejection'));

async function getSocket() {
  if (sock && connected) return sock;
  if (sock) {
    try {
      sock.end(undefined);
    } catch (e) {}
    sock = null;
  }

  const { version } = await fetchLatestBaileysVersion();

  const { state: authState, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

  sock = makeWaSocket({
    version,
    logger,
    auth: authState,
    // Use standard WA Web fingerprint — custom names are a ban signal
    browser: ['Mac OS', 'Chrome', '131.0.6778.204'],
    // Don't appear permanently online — only show online when actively chatting
    markOnlineOnConnect: false,
    // Don't pull full chat history on connect (reduces server-side scrutiny)
    syncFullHistory: false,
    getMessage: async (key) => {
      return msgCache.get(key.id) || { conversation: '' };
    },
  });

  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', handleConnectionUpdate);
  sock.ev.on('messages.upsert', handleMessagesUpsert);

  return sock;
}

async function handleConnectionUpdate(update) {
  const { connection, lastDisconnect, qr } = update;

  logger.info({ connection, qr: qr ? 'yes' : 'no' }, 'Connection update');

  if (qr) {
    qrCode = await QRCode.toDataURL(qr);
    logger.info('QR code updated — scan with WhatsApp on your phone');
    // If pairing code flow active and WA rotated back to QR (code expired), auto-refresh
    if (pairingInProgress && pairingPhoneNumber && sock) {
      try {
        const code = await sock.requestPairingCode(pairingPhoneNumber);
        pairingCode = code;
        logger.info({ code }, 'Pairing code auto-refreshed');
      } catch (e) {
        logger.error({ error: e.message }, 'Auto-refresh pairing code failed');
      }
    }
  }

  if (connection === 'close') {
    connected = false;
    const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
    const reasonText = DisconnectReason[reason] || 'unknown';
    // 401 = loggedOut (user delinked device) — don't reconnect, need fresh auth
    // 408 = QR timeout / connection reset — reconnect with new QR
    // Everything else — reconnect
    const shouldReconnect = reason !== DisconnectReason.loggedOut;

    logger.error({ reason, reasonText }, 'Connection closed');

    // 515 = restartRequired — normal after pairing completes, always reconnect
    const isRestartRequired = reason === 515;
    if (isRestartRequired) pairingInProgress = false;

    if (shouldReconnect && (!pairingInProgress || isRestartRequired)) {
      const delay = reconnectDelay();
      logger.info({ delay }, `Reconnecting in ${delay}ms...`);
      sock = null;
      setTimeout(() => getSocket().catch(e => logger.error({ error: e.message }, 'Reconnect failed')), delay);
    } else if (!shouldReconnect) {
      logger.warn('Logged out — clear .baileys_auth/ and restart to re-pair');
      sock = null;
    }
  } else if (connection === 'open') {
    connected = true;
    pairingInProgress = false;
    pairingPhoneNumber = null;
    resetReconnect();
    logger.info('Connected to WhatsApp!');
    qrCode = null;
    pairingCode = null;

    if (sock?.user?.id) {
      const myJid = sock.user.id;
      logger.info({ jid: myJid }, 'Sending hello to own number');
      try {
        const helloSent = await sock.sendMessage(myJid, { text: '👋 Hello! WhatsApp bridge connected successfully. You can now chat with G4L from WhatsApp!' });
        if (helloSent?.key?.id) sentIds.add(helloSent.key.id);
        logger.info('Hello message sent');
      } catch (e) {
        logger.error({ error: e.message }, 'Failed to send hello');
      }
    }
  }
}

async function handleMessagesUpsert({ messages }) {
  for (const msg of messages) {
    const msgId = msg.key?.id;
    if (msgId && msg.message) msgCache.set(msgId, msg.message);

    // Skip echo of our own sent messages to avoid loop; allow user's fromMe self-chat messages
    if (sentIds.has(msgId)) continue;

    const remoteJid = msg.key.remoteJid;
    const messageText = msg.message?.conversation || msg.message?.extendedTextMessage?.text || msg.message?.buttonsResponseMessage?.selectedButtonId || '';

    if (!remoteJid || remoteJid === 'status@broadcast') continue;
    if (!messageText) continue;

    logger.info({ from: remoteJid, fromMe: msg.key.fromMe, text: messageText }, 'Incoming message');
    // Persist to local archive (~/.clixen/whatsapp.db) for offline search.
    logIncoming(msg, messageText).catch(() => {});
    await handleIncomingMessage(msg);
  }
}

async function handleIncomingMessage(msg) {
  const remoteJid = msg.key.remoteJid;
  const messageText = msg.message.conversation || msg.message.extendedTextMessage?.text || '';

  // Mark message as read immediately (human behaviour)
  try {
    await sock.readMessages([msg.key]);
  } catch (_) {}

  // Show "typing…" so we look human while the LLM thinks
  try {
    await sock.sendPresenceUpdate('composing', remoteJid);
  } catch (_) {}

  try {
    const response = await fetch(`${BOT_URL}/webhook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sender: remoteJid,
        message: messageText,
        name: msg.pushName || remoteJid.split('@')[0],
      }),
    });

    if (!response.ok) {
      throw new Error(`Bot responded with ${response.status}`);
    }

    const replyData = await response.json();
    const replyText = replyData.reply || replyData.message || '';

    if (replyText) {
      // Stop typing indicator
      try { await sock.sendPresenceUpdate('paused', remoteJid); } catch (_) {}

      // Rate-limit guard
      if (isRateLimited(remoteJid)) {
        logger.warn({ jid: remoteJid }, 'Rate limit hit — dropping reply');
        return;
      }

      // Human-like delay before sending (proportional to reply length, 800ms–3.5s)
      const charDelay = Math.min(replyText.length * 30, 3500);
      await jitter(800, Math.max(800, charDelay));

      const sent = await sock.sendMessage(remoteJid, { text: replyText });
      if (sent?.key?.id) sentIds.add(sent.key.id);
      logger.info({ to: remoteJid, length: replyText.length }, 'Sent reply');
      logOutgoing(remoteJid, replyText, sent?.key?.id).catch(() => {});
    }
  } catch (error) {
    try { await sock.sendPresenceUpdate('paused', remoteJid); } catch (_) {}
    logger.error({ error }, 'Failed to process message');
  }
}

app.get('/auth/qr', async (req, res) => {
  if (connected) {
    return res.send(`
      <!DOCTYPE html>
      <html><head><title>WhatsApp Connected</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp Connected</h1>
        <p style="color:green">✓ Your WhatsApp is connected and ready.</p>
      </body></html>
    `);
  }

  if (!qrCode) {
    return res.send(`
      <!DOCTYPE html>
      <html><head><title>WhatsApp QR</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp QR Code</h1>
        <p>Waiting for QR code...</p>
      </body></html>
    `);
  }

  res.send(`
    <!DOCTYPE html>
    <html><head><title>WhatsApp QR</title></head>
    <body style="font-family:system-ui;padding:40px;text-align:center">
      <h1>WhatsApp QR Code</h1>
      <p style="margin-bottom:20px">Scan this with WhatsApp on your phone</p>
      <img src="${qrCode}" alt="QR Code" style="max-width:300px;border:1px solid #ccc;border-radius:8px;padding:10px">
      <p style="margin-top:20px;color:#666">Or use <a href="/auth/pairing-code">pairing code</a></p>
    </body></html>
  `);
});

app.get('/api/qr-json', async (req, res) => {
  res.json({
    status: connected ? 'connected' : 'pending',
    qr: qrCode,
    connected: connected,
  });
});

app.post('/auth/pairing-code', async (req, res) => {
  const phoneNumber = req.body.phoneNumber || req.query.phoneNumber || '';
  if (!phoneNumber) return res.status(400).json({ error: 'phoneNumber required' });

  if (connected) return res.json({ status: 'already_connected' });

  pairingInProgress = true;
  pairingPhoneNumber = phoneNumber;
  try {
    // Re-create socket without QR (pairing code and QR are mutually exclusive in v7)
    if (sock) {
      try { sock.end(undefined); } catch (_) {}
      sock = null;
      connected = false;
    }

    const { version } = await fetchLatestBaileysVersion();
    const { state: authState, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    sock = makeWaSocket({
      version,
      logger,
      auth: authState,
      browser: ['Mac OS', 'Chrome', '131.0.6778.204'],
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
      getMessage: async (key) => msgCache.get(key.id) || { conversation: '' },
    });

    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', handleConnectionUpdate);
    sock.ev.on('messages.upsert', handleMessagesUpsert);

    // Wait for socket to be ready then request pairing code
    await new Promise(r => setTimeout(r, 2000));
    const code = await sock.requestPairingCode(phoneNumber);
    pairingCode = code;
    logger.info({ code }, 'Pairing code generated');
    res.json({ code });
  } catch (error) {
    pairingInProgress = false;
    pairingPhoneNumber = null;
    logger.error({ error: error.message }, 'Pairing code error');
    res.status(500).json({ error: error.message });
  }
});

app.get('/auth/pairing-code', async (req, res) => {
  if (connected) {
    return res.send(`
      <!DOCTYPE html>
      <html><head><title>WhatsApp Connected</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp Connected</h1>
        <p style="color:green">✓ Your WhatsApp is connected and ready.</p>
      </body></html>
    `);
  }

  if (pairingCode) {
    // Format as XXXX-XXXX
    const formatted = pairingCode.length === 8
      ? `${pairingCode.slice(0,4)}-${pairingCode.slice(4)}`
      : pairingCode;
    return res.send(`
      <!DOCTYPE html>
      <html><head><title>WhatsApp Pairing Code</title></head>
      <body style="font-family:system-ui;padding:40px;text-align:center">
        <h1>WhatsApp Pairing Code</h1>
        <p style="margin-bottom:20px">Enter this code in WhatsApp on your phone</p>
        <div style="font-size:52px;font-weight:bold;letter-spacing:6px;padding:20px 32px;border:2px solid #25D366;border-radius:12px;display:inline-block;background:#f6fff9;color:#111;">${formatted}</div>
        <p style="margin-top:20px;color:#555">In WhatsApp: <strong>Settings → Linked Devices → Link a Device → Link with phone number</strong></p>
        <p style="margin-top:12px"><a href="/auth/qr">← Use QR code instead</a></p>
      </body></html>
    `);
  }

  const phone = process.env.WHATSAPP_DEFAULT_TARGET
    ? process.env.WHATSAPP_DEFAULT_TARGET.replace(/^\+/, '')
    : '';

  res.send(`
    <!DOCTYPE html>
    <html><head><title>WhatsApp Pairing Code</title>
    <script>
      async function requestCode() {
        const phone = document.getElementById('phone').value.replace(/[^0-9]/g,'');
        const btn = document.getElementById('btn');
        btn.disabled = true; btn.textContent = 'Requesting…';
        try {
          const r = await fetch('/auth/pairing-code', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({phoneNumber: phone})
          });
          const d = await r.json();
          if (d.code) {
            const fmt = d.code.length === 8 ? d.code.slice(0,4)+'-'+d.code.slice(4) : d.code;
            document.getElementById('result').innerHTML =
              '<div style="font-size:52px;font-weight:bold;letter-spacing:6px;padding:20px 32px;border:2px solid #25D366;border-radius:12px;display:inline-block;background:#f6fff9;color:#111;">'+fmt+'</div>' +
              '<p style="margin-top:16px;color:#555">In WhatsApp: <strong>Settings → Linked Devices → Link a Device → Link with phone number</strong></p>';
          } else {
            document.getElementById('result').innerHTML = '<p style="color:red">'+(d.error||JSON.stringify(d))+'</p>';
            btn.disabled = false; btn.textContent = 'Get Pairing Code';
          }
        } catch(e) {
          document.getElementById('result').innerHTML = '<p style="color:red">'+e.message+'</p>';
          btn.disabled = false; btn.textContent = 'Get Pairing Code';
        }
      }
    </script>
    </head>
    <body style="font-family:system-ui;padding:40px;text-align:center">
      <h1>Link WhatsApp</h1>
      <p style="color:#555">Enter your phone number (with country code, no +)</p>
      <input id="phone" type="tel" value="${phone}" placeholder="18574261739"
        style="font-size:20px;padding:10px 16px;border:1px solid #ccc;border-radius:8px;width:220px;text-align:center;margin-bottom:16px">
      <br>
      <button id="btn" onclick="requestCode()"
        style="font-size:18px;padding:12px 28px;background:#25D366;color:white;border:none;border-radius:8px;cursor:pointer">
        Get Pairing Code
      </button>
      <div id="result" style="margin-top:28px"></div>
      <p style="margin-top:32px"><a href="/auth/qr">← Use QR code instead</a></p>
    </body></html>
  `);
});

app.post('/auth/reset', async (req, res) => {
  try {
    if (sock) {
      sock.ws.close();
      sock = null;
    }
    connected = false;
    qrCode = null;
    pairingCode = null;

    res.json({ status: 'reset' });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/status', (req, res) => {
  res.json({
    status: connected ? 'connected' : 'disconnected',
    botUrl: BOT_URL,
    qrAvailable: !!qrCode,
    sessionExists: existsSync(join(SESSION_DIR, 'creds.json')),
  });
});

app.post('/send', async (req, res) => {
  if (!connected) {
    return res.status(503).json({ error: 'Not connected to WhatsApp' });
  }

  const { to, message } = req.body;

  if (!to || !message) {
    return res.status(400).json({ error: 'Missing to or message' });
  }

  try {
    if (isRateLimited(to)) {
      return res.status(429).json({ error: 'Rate limit exceeded — slow down' });
    }
    await jitter(400, 1200);
    const sent = await sock.sendMessage(to, { text: message });
    if (sent?.key?.id) { msgCache.set(sent.key.id, { conversation: message }); sentIds.add(sent.key.id); }
    logOutgoing(to, message, sent?.key?.id).catch(() => {});
    res.json({ status: 'sent', to, message });
  } catch (error) {
    logger.error({ error }, 'Failed to send message');
    res.status(500).json({ error: error.message });
  }
});

async function main() {
  try {
    await getSocket();

    app.listen(PORT, () => {
      logger.info({ port: PORT, botUrl: BOT_URL }, 'WhatsApp bridge listening');
    });
  } catch (error) {
    console.error('Failed to start bridge:', error);
    logger.fatal({ error: error.message, stack: error.stack }, 'Failed to start bridge');
    process.exit(1);
  }
}

main().catch((error) => {
  logger.fatal({ error }, 'Failed to start bridge');
  process.exit(1);
});