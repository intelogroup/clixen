// WhatsApp message logger — writes incoming + outgoing messages to a local
// SQLite DB with FTS5 so Python tools/whatsapp_search.py can query offline.
//
// DB path: ~/.clixen/whatsapp.db
// Schema:
//   messages(id PK, msg_id UNIQUE, jid, push_name, from_me INTEGER, text, ts INTEGER)
//   message_fts(text)  -- FTS5 mirror, kept in sync via triggers
//
// Designed to fail soft: if better-sqlite3 isn't installed, every log call is
// a no-op so the bridge keeps running. Add to package.json deps to enable:
//     "better-sqlite3": "^11.5.0"

import { homedir } from 'os';
import { join } from 'path';
import { mkdirSync, existsSync } from 'fs';

const DB_DIR = join(homedir(), '.clixen');
const DB_PATH = join(DB_DIR, 'whatsapp.db');

let db = null;
let insertStmt = null;
let initFailed = false;

function ensureDir() {
  if (!existsSync(DB_DIR)) {
    mkdirSync(DB_DIR, { recursive: true });
  }
}

async function initDb() {
  if (db || initFailed) return db;
  try {
    ensureDir();
    const { default: Database } = await import('better-sqlite3');
    db = new Database(DB_PATH);
    db.pragma('journal_mode = WAL');
    db.exec(`
      CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        msg_id      TEXT UNIQUE,
        jid         TEXT,
        push_name   TEXT,
        from_me     INTEGER NOT NULL DEFAULT 0,
        text        TEXT,
        ts          INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_messages_jid ON messages(jid);
      CREATE INDEX IF NOT EXISTS idx_messages_ts  ON messages(ts);

      CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
        text, content='messages', content_rowid='id'
      );

      CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO message_fts(rowid, text) VALUES (new.id, new.text);
      END;
      CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
        INSERT INTO message_fts(message_fts, rowid, text) VALUES('delete', old.id, old.text);
      END;
      CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
        INSERT INTO message_fts(message_fts, rowid, text) VALUES('delete', old.id, old.text);
        INSERT INTO message_fts(rowid, text) VALUES (new.id, new.text);
      END;
    `);
    insertStmt = db.prepare(
      'INSERT OR IGNORE INTO messages(msg_id, jid, push_name, from_me, text, ts) ' +
      'VALUES (@msg_id, @jid, @push_name, @from_me, @text, @ts)'
    );
    return db;
  } catch (err) {
    initFailed = true;
    // Don't crash the bridge — just log once and keep no-op'ing.
    console.error('[whatsapp_log] init failed (logging disabled):', err.message);
    return null;
  }
}

/**
 * Log an incoming message (parsed from messages.upsert).
 * msg = baileys WAMessage. Pass through whatever is convenient.
 */
export async function logIncoming(msg, text) {
  if (initFailed) return;
  if (!db) await initDb();
  if (!db || !insertStmt) return;
  if (!text) return;
  try {
    insertStmt.run({
      msg_id: msg?.key?.id || null,
      jid: msg?.key?.remoteJid || '',
      push_name: msg?.pushName || '',
      from_me: msg?.key?.fromMe ? 1 : 0,
      text,
      ts: Math.floor(Date.now() / 1000),
    });
  } catch (err) {
    console.error('[whatsapp_log] insert failed:', err.message);
  }
}

/**
 * Log an outgoing message (after sock.sendMessage succeeds).
 */
export async function logOutgoing(jid, text, msgId) {
  if (initFailed) return;
  if (!db) await initDb();
  if (!db || !insertStmt) return;
  if (!text) return;
  try {
    insertStmt.run({
      msg_id: msgId || null,
      jid: jid || '',
      push_name: '',
      from_me: 1,
      text,
      ts: Math.floor(Date.now() / 1000),
    });
  } catch (err) {
    console.error('[whatsapp_log] insert failed:', err.message);
  }
}
