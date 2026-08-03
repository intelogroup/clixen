#!/usr/bin/env node
import { google } from 'googleapis';
import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import path from 'path';

const CREDENTIALS_PATH = './credentials.json';
const TOKEN_PATH = './token.json';
const BASE_DIR = process.env.ATTACHMENT_DOWNLOAD_DIR || path.join(process.env.HOME || '.', 'Downloads', 'clixen-attachments');

const creds = JSON.parse(readFileSync(CREDENTIALS_PATH, 'utf8'));
const { client_id, client_secret } = creds.web;
const token = JSON.parse(readFileSync(TOKEN_PATH, 'utf8'));

const auth = new google.auth.OAuth2(client_id, client_secret, 'http://localhost:3333');
auth.setCredentials(token);
const gmail = google.gmail({ version: 'v1', auth });

const financeKeywords = /budget|financ|payroll|wire transfer|salary|salaire|rapport financ|depenses|dépenses|invoice|facture|paiement|payment|fund|grant|cotisation|compte|comptabilit|tresor|trésor|audit|bilan|devis|quotation|cost|coût|prix|price|achat|purchase|procurement|don|donation/i;
const mgmtKeywords = /board|meeting|réunion|reunion|statut|bylaw|staff|employee|employé|hr|ressource|resource|recrutement|recruitment|contrat|contract|mandat|direction|directeur|director|gestion|management|admin|governance|gouvernance|comité|committee|coordination|rapport de|minutes|compte.rendu/i;
const projectKeywords = /project|projet|plan|programme|program|phase|milestone|deliverable|livrable|suivi|update|progress|rapport|report|site web|website|lab|digital|construction|installation|equipment|équipement|medical|clinic|hospital|hôpital|hopital|ZL|HUM|solar|oxygen|formation|training/i;

function categorize(subject, snippet) {
  const combined = (subject + ' ' + snippet).toLowerCase();
  if (financeKeywords.test(combined)) return 'Finance';
  if (mgmtKeywords.test(combined)) return 'Management';
  if (projectKeywords.test(combined)) return 'Project';
  return null;
}

function sanitizeFilename(name) {
  return name.replace(/[/\\?%*:|"<>]/g, '-');
}

async function fetchAllMessages() {
  let messages = [];
  let pageToken;
  do {
    const res = await gmail.users.messages.list({
      userId: 'me',
      q: 'from:maxi has:attachment after:2020/1/1',
      maxResults: 500,
      pageToken,
    });
    messages = messages.concat(res.data.messages || []);
    pageToken = res.data.nextPageToken;
    console.log(`Fetched ${messages.length} message IDs so far...`);
  } while (pageToken);
  return messages;
}

async function downloadAttachments() {
  const messages = await fetchAllMessages();
  console.log(`\nTotal messages to process: ${messages.length}`);

  let downloaded = 0, skipped = 0;
  const log = [];

  for (let i = 0; i < messages.length; i++) {
    const { id } = messages[i];
    const msg = await gmail.users.messages.get({ userId: 'me', id, format: 'full' });
    const headers = msg.data.payload?.headers || [];
    const subject = headers.find(h => h.name === 'Subject')?.value || '(no subject)';
    const date = headers.find(h => h.name === 'Date')?.value || '';
    const snippet = msg.data.snippet || '';

    const category = categorize(subject, snippet);
    if (!category) { skipped++; continue; }

    const folder = path.join(BASE_DIR, category);
    mkdirSync(folder, { recursive: true });

    const parts = msg.data.payload?.parts || [];
    for (const part of parts) {
      if (!part.filename || !part.body) continue;
      const attachmentId = part.body.attachmentId;
      if (!attachmentId) continue;

      const att = await gmail.users.messages.attachments.get({
        userId: 'me', messageId: id, id: attachmentId,
      });

      const data = Buffer.from(att.data.data, 'base64url');
      const safeDate = new Date(date).toISOString().split('T')[0] || 'unknown';
      const safeName = sanitizeFilename(`${safeDate}_${part.filename}`);
      const filePath = path.join(folder, safeName);
      writeFileSync(filePath, data);
      console.log(`[${i+1}/${messages.length}] [${category}] ${safeName}`);
      log.push({ category, date: safeDate, subject, filename: safeName });
      downloaded++;
    }

    // Polite rate limiting
    if (i % 10 === 9) await new Promise(r => setTimeout(r, 500));
  }

  writeFileSync(path.join(BASE_DIR, 'download-log.json'), JSON.stringify(log, null, 2));
  console.log(`\n✅ Done. Downloaded: ${downloaded}, Skipped (uncategorized): ${skipped}`);
  console.log(`Log saved to ${BASE_DIR}/download-log.json`);
}

downloadAttachments().catch(console.error);
