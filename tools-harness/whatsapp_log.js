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
let upsertContactStmt = null;
let ownerJidStmt = null;
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
        role        TEXT NOT NULL DEFAULT 'them',
        speaker_name TEXT,
        text        TEXT,
        ts          INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_messages_jid ON messages(jid);
      CREATE INDEX IF NOT EXISTS idx_messages_ts  ON messages(ts);

      CREATE TABLE IF NOT EXISTS contacts (
        jid           TEXT PRIMARY KEY,
        lid           TEXT,
        name          TEXT,
        notify        TEXT,
        verified_name TEXT,
        last_seen_at  INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name);

      CREATE TABLE IF NOT EXISTS owner_jids (
        jid TEXT PRIMARY KEY
      );

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
    // Migrate archives created before explicit speaker attribution existed.
    for (const statement of [
      "ALTER TABLE messages ADD COLUMN role TEXT NOT NULL DEFAULT 'them'",
      'ALTER TABLE messages ADD COLUMN speaker_name TEXT',
    ]) {
      try { db.exec(statement); } catch (err) {
        if (!String(err?.message || err).includes('duplicate column name')) throw err;
      }
    }
    db.exec("UPDATE messages SET role = CASE WHEN from_me = 1 THEN 'me' ELSE 'them' END, speaker_name = CASE WHEN from_me = 1 THEN 'me' ELSE COALESCE(NULLIF(push_name, ''), 'them') END");
    insertStmt = db.prepare(
      'INSERT INTO messages(msg_id, jid, push_name, from_me, role, speaker_name, text, ts) ' +
      'VALUES (@msg_id, @jid, @push_name, @from_me, @role, @speaker_name, @text, @ts) ' +
      'ON CONFLICT(msg_id) DO UPDATE SET ' +
      'jid=excluded.jid, push_name=excluded.push_name, from_me=excluded.from_me, ' +
      'role=excluded.role, speaker_name=excluded.speaker_name, text=excluded.text, ts=excluded.ts'
    );
    upsertContactStmt = db.prepare(
      'INSERT INTO contacts(jid, lid, name, notify, verified_name, last_seen_at) ' +
      'VALUES (@jid, @lid, @name, @notify, @verified_name, @last_seen_at) ' +
      'ON CONFLICT(jid) DO UPDATE SET ' +
      'lid=COALESCE(excluded.lid, contacts.lid), ' +
      'name=COALESCE(excluded.name, contacts.name), ' +
      'notify=COALESCE(excluded.notify, contacts.notify), ' +
      'verified_name=COALESCE(excluded.verified_name, contacts.verified_name), ' +
      'last_seen_at=MAX(contacts.last_seen_at, excluded.last_seen_at)'
    );
    ownerJidStmt = db.prepare('INSERT OR IGNORE INTO owner_jids(jid) VALUES (?)');
    return db;
  } catch (err) {
    initFailed = true;
    // Don't crash the bridge — just log once and keep no-op'ing.
    console.error('[whatsapp_log] init failed (logging disabled):', err.message);
    return null;
  }
}

/**
 * Whether the local archive (message history, @ai context lookup, contact
 * resolution) is actually working. False after a native-module load failure
 * (e.g. better-sqlite3 built against a different node than the one running
 * this process) — every write/read silently no-ops in that state, so this is
 * the only way to notice without reading stderr.
 */
export async function archiveAvailable() {
  if (!db && !initFailed) await initDb();
  return !initFailed;
}

export async function loadContacts() {
  if (initFailed) return [];
  if (!db) await initDb();
  if (!db) return [];
  try {
    return db.prepare(
      'SELECT jid, lid, name, notify, verified_name AS verifiedName, last_seen_at AS lastSeenAt ' +
      'FROM contacts ORDER BY COALESCE(name, notify, jid) COLLATE NOCASE'
    ).all();
  } catch (err) {
    console.error('[whatsapp_log] contact select failed:', err.message);
    return [];
  }
}

export async function logOwnerJids(jids) {
  if (initFailed) return;
  if (!db) await initDb();
  if (!db || !ownerJidStmt) return;
  try {
    for (const jid of jids || []) {
      if (jid) ownerJidStmt.run(String(jid).replace(/:\d+@/, '@'));
    }
  } catch (err) {
    console.error('[whatsapp_log] owner jid save failed:', err.message);
  }
}

export async function logContact(contact) {
  if (initFailed) return;
  if (!db) await initDb();
  if (!db || !upsertContactStmt) return;
  try {
    upsertContactStmt.run({
      jid: contact?.jid || '',
      lid: contact?.lid || null,
      name: contact?.name || null,
      notify: contact?.notify || null,
      verified_name: contact?.verifiedName || null,
      last_seen_at: contact?.lastSeenAt || Math.floor(Date.now() / 1000),
    });
  } catch (err) {
    console.error('[whatsapp_log] contact upsert failed:', err.message);
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
    const fromMe = msg?.key?.fromMe ? 1 : 0;
    const rawTimestamp = Number(msg?.messageTimestamp ?? 0);
    insertStmt.run({
      msg_id: msg?.key?.id || null,
      jid: msg?.key?.remoteJid || '',
      push_name: msg?.pushName || '',
      from_me: fromMe,
      role: fromMe ? 'me' : 'them',
      speaker_name: fromMe ? 'me' : (msg?.pushName || 'them'),
      text,
      ts: rawTimestamp > 0 ? rawTimestamp : Math.floor(Date.now() / 1000),
    });
  } catch (err) {
    console.error('[whatsapp_log] insert failed:', err.message);
  }
}

/**
 * Fetch the last `limit` messages for a chat jid, oldest first, for @ai context injection.
 */
export async function fetchRecentHistory(jid, limit = 20) {
  if (initFailed) return [];
  if (!db) await initDb();
  if (!db) return [];
  try {
    const rows = db.prepare(
      'SELECT push_name, from_me, role, speaker_name, text, ts FROM messages WHERE jid = @jid ORDER BY ts DESC, id DESC LIMIT @limit'
    ).all({ jid, limit });
    return rows.reverse();
  } catch (err) {
    console.error('[whatsapp_log] history select failed:', err.message);
    return [];
  }
}

/** Resolve a contact name/number to the JID used by the local message archive. */
export async function findContactJid(query) {
  if (initFailed) return null;
  if (!db) await initDb();
  if (!db || !query) return null;
  try {
    const value = String(query).trim();
    const exact = db.prepare(
      `SELECT jid FROM contacts
       WHERE lower(COALESCE(name, '')) = lower(@value)
          OR lower(COALESCE(notify, '')) = lower(@value)
          OR jid = @value
       ORDER BY last_seen_at DESC LIMIT 1`
    ).get({ value });
    if (exact?.jid) return exact.jid;

    const partial = db.prepare(
      `SELECT jid FROM contacts
       WHERE lower(COALESCE(name, '')) LIKE lower(@pattern)
          OR lower(COALESCE(notify, '')) LIKE lower(@pattern)
       ORDER BY last_seen_at DESC LIMIT 1`
    ).get({ pattern: `%${value}%` });
    if (partial?.jid) return partial.jid;

    // Contacts may not have been synced yet; message push names are a useful
    // fallback for people who have already sent or received a message.
    const fromMessages = db.prepare(
      `SELECT jid FROM messages
       WHERE lower(COALESCE(push_name, '')) = lower(@value)
       ORDER BY ts DESC, id DESC LIMIT 1`
    ).get({ value });
    return fromMessages?.jid ?? null;
  } catch (err) {
    console.error('[WA-PRIVATE] contact resolve failed:', err.message);
    return null;
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
      role: 'me',
      speaker_name: 'me',
      text,
      ts: Math.floor(Date.now() / 1000),
    });
  } catch (err) {
    console.error('[whatsapp_log] insert failed:', err.message);
  }
}
