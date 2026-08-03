#!/usr/bin/env node
// Run this once to authorize Gmail access
import { google } from 'googleapis';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { createServer } from 'http';
import { URL } from 'url';


const CREDENTIALS_PATH = './credentials.json';
const TOKEN_PATH = './token.json';
const SCOPES = [
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/gmail.send',
];

const creds = JSON.parse(readFileSync(CREDENTIALS_PATH, 'utf8'));
const { client_id, client_secret, redirect_uris } = creds.installed || creds.web;

const oauth2Client = new google.auth.OAuth2(
  client_id,
  client_secret,
  'http://localhost:3333'
);

const authUrl = oauth2Client.generateAuthUrl({
  access_type: 'offline',
  scope: SCOPES,
  prompt: 'consent',
});

console.log('Opening browser for Gmail authorization...');
console.log('\nIf the browser does not open, visit:\n', authUrl);

// Try to open the browser
try {
  const { execSync } = await import('child_process');
  execSync(`open "${authUrl}"`);
} catch {}

// Start a local server to catch the redirect
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost:3333');
  const code = url.searchParams.get('code');

  if (!code) {
    res.end('No code received');
    return;
  }

  try {
    const { tokens } = await oauth2Client.getToken(code);
    writeFileSync(TOKEN_PATH, JSON.stringify(tokens, null, 2));
    res.end('<h2>✅ Gmail authorized! You can close this tab.</h2>');
    console.log('\n✅ Token saved to token.json');
    server.close();
    process.exit(0);
  } catch (err) {
    res.end('Error: ' + err.message);
    console.error(err);
    server.close();
    process.exit(1);
  }
});

server.listen(3333, () => {
  console.log('\nWaiting for authorization on http://localhost:3333 ...');
});
