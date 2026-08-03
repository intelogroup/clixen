#!/usr/bin/env node
/**
 * Batch attachment downloader — uses pre-categorized message IDs from /tmp/maxi_all.json
 * Downloads into: zl_master_board/finance | /projects | /management
 */
import { google } from 'googleapis';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import path from 'path';

const CREDENTIALS_PATH = '/Users/kalinovdameus/developer/zl_master_board/gmail-mcp/credentials.json';
const TOKEN_PATH       = '/Users/kalinovdameus/developer/zl_master_board/gmail-mcp/token.json';
const CATEGORIZED_PATH = '/tmp/maxi_all.json';
const REPO_BASE        = '/Users/kalinovdameus/Developer/zl_master_board';
const LOG_PATH         = '/tmp/maxi_download_log.json';

const FOLDER_MAP = {
  finance:    path.join(REPO_BASE, 'finance'),
  management: path.join(REPO_BASE, 'management'),
  projects:   path.join(REPO_BASE, 'projects'),
};

// Ensure folders exist
Object.values(FOLDER_MAP).forEach(f => mkdirSync(f, { recursive: true }));

const creds = JSON.parse(readFileSync(CREDENTIALS_PATH, 'utf8'));
const token = JSON.parse(readFileSync(TOKEN_PATH, 'utf8'));
const auth  = new google.auth.OAuth2(creds.web.client_id, creds.web.client_secret, 'http://localhost:3333');
auth.setCredentials(token);
const gmail = google.gmail({ version: 'v1', auth });

function sanitize(name) {
  return name.replace(/[/\\?%*:|"<>]/g, '-').replace(/\s+/g, '_').slice(0, 120);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Collect all attachment parts recursively from a message payload
function collectParts(payload, parts = []) {
  if (!payload) return parts;
  if (payload.filename && payload.filename.length > 0 && payload.body?.attachmentId) {
    parts.push(payload);
  }
  if (payload.parts) {
    for (const p of payload.parts) collectParts(p, parts);
  }
  return parts;
}

async function downloadAttachment(msgId, attachmentId, destPath) {
  const res = await gmail.users.messages.attachments.get({
    userId: 'me',
    messageId: msgId,
    id: attachmentId,
  });
  const data = res.data.data.replace(/-/g, '+').replace(/_/g, '/');
  const buf  = Buffer.from(data, 'base64');
  writeFileSync(destPath, buf);
}

async function processCategory(catName, messages) {
  const folder = FOLDER_MAP[catName];
  let downloaded = 0, skipped = 0, errors = 0;
  const log = [];

  const BATCH = 5; // concurrent requests
  for (let i = 0; i < messages.length; i += BATCH) {
    const batch = messages.slice(i, i + BATCH);
    await Promise.all(batch.map(async ({ id, subject, date }) => {
      try {
        const msg = await gmail.users.messages.get({ userId: 'me', id, format: 'full' });
        const parts = collectParts(msg.data.payload);
        if (parts.length === 0) { skipped++; return; }

        const dateStr = date ? date.slice(0, 10).replace(/\//g, '-') : 'unknown';
        const subjectSafe = sanitize(subject || 'no-subject');

        for (const part of parts) {
          const ext = path.extname(part.filename) || '';
          const fname = `${dateStr}_${subjectSafe}_${sanitize(part.filename)}`;
          const dest  = path.join(folder, fname);

          if (existsSync(dest)) { skipped++; continue; }

          await downloadAttachment(id, part.body.attachmentId, dest);
          downloaded++;
          log.push({ cat: catName, file: fname, msgId: id });
        }
      } catch (err) {
        errors++;
        log.push({ cat: catName, msgId: id, error: err.message });
      }
    }));

    const pct = Math.round(((i + BATCH) / messages.length) * 100);
    process.stdout.write(`\r[${catName}] ${Math.min(i + BATCH, messages.length)}/${messages.length} (${pct}%) — ↓${downloaded} skip:${skipped} err:${errors}   `);
    await sleep(200); // rate limit courtesy
  }

  console.log(`\n[${catName}] DONE — Downloaded: ${downloaded}, Skipped: ${skipped}, Errors: ${errors}`);
  return { downloaded, skipped, errors, log };
}

async function main() {
  const { categorized } = JSON.parse(readFileSync(CATEGORIZED_PATH, 'utf8'));

  console.log('=== Maxi Attachment Downloader → Repo Folders ===');
  console.log(`Finance: ${categorized.finance.length} emails`);
  console.log(`Management: ${categorized.management.length} emails`);
  console.log(`Projects: ${categorized.projects.length} emails`);
  console.log('');

  const allLogs = {};

  for (const [cat, msgs] of Object.entries(categorized)) {
    if (!FOLDER_MAP[cat] || msgs.length === 0) continue;
    console.log(`\n--- Processing ${cat} (${msgs.length} emails) ---`);
    allLogs[cat] = await processCategory(cat, msgs);
  }

  writeFileSync(LOG_PATH, JSON.stringify(allLogs, null, 2));

  const totalDl  = Object.values(allLogs).reduce((s, v) => s + v.downloaded, 0);
  const totalSkip = Object.values(allLogs).reduce((s, v) => s + v.skipped, 0);
  const totalErr = Object.values(allLogs).reduce((s, v) => s + v.errors, 0);

  console.log('\n=== COMPLETE ===');
  console.log(`Total downloaded: ${totalDl}`);
  console.log(`Total skipped:    ${totalSkip}`);
  console.log(`Total errors:     ${totalErr}`);
  console.log(`Log saved to:     ${LOG_PATH}`);
}

main().catch(err => { console.error(err); process.exit(1); });
