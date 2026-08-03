#!/usr/bin/env node
/**
 * Clixen Screenshot Helper Tool (.mjs)
 * Takes a screenshot (full-screen or interactive) and uploads/sends it to Clixen agent.
 */

import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import os from 'os';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function getWorkspaceState() {
  const envPath = process.env.G4L_WORKSPACE_STATE_PATH;
  const pathsToTry = envPath 
    ? [path.resolve(envPath)] 
    : [
        path.join(__dirname, '../tools-harness/workspace_state.json'),
        path.join(__dirname, '../workspace_state.json'),
        path.join(process.cwd(), 'tools-harness/workspace_state.json'),
        path.join(process.cwd(), 'workspace_state.json'),
      ];

  let statePath = null;
  for (const p of pathsToTry) {
    if (fs.existsSync(p)) {
      statePath = p;
      break;
    }
  }

  if (!statePath) {
    console.error(`Error: Could not locate workspace_state.json in paths: ${pathsToTry}`);
    process.exit(1);
  }

  try {
    const data = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    const account = data.account;
    if (!account || !account.email) {
      console.error(`Error: No user account found in ${statePath}`);
      process.exit(1);
    }
    const secret = data.session_secret;
    if (!secret) {
      console.error(`Error: session_secret not found in ${statePath}`);
      process.exit(1);
    }
    return { email: account.email, secret };
  } catch (err) {
    console.error(`Error reading workspace state from ${statePath}: ${err.message}`);
    process.exit(1);
  }
}

function signSession(email, secret) {
  const payload = {
    email: email,
    exp: Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 30, // 30 days
  };
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const sig = crypto.createHmac('sha256', secret).update(body).digest('base64url');
  return `${body}.${sig}`;
}

function captureScreen(interactive) {
  const tempPath = path.join(os.tmpdir(), `shot-${Date.now()}.png`);
  const cmd = interactive 
    ? `/usr/sbin/screencapture -i "${tempPath}"` 
    : `/usr/sbin/screencapture -x "${tempPath}"`;

  if (interactive) {
    console.log('Select screen area or window to capture (or press ESC to cancel)...');
  } else {
    process.stdout.write('Capturing full screen...');
  }

  try {
    execSync(cmd, { stdio: 'inherit' });
    if (!interactive) {
      console.log(' Done.');
    }
    return tempPath;
  } catch (err) {
    console.error('\nCapture failed or was cancelled.');
    process.exit(1);
  }
}

async function uploadScreenshot(url, token, filePath, chatId) {
  const uploadUrl = `${url.replace(/\/$/, '')}/upload`;
  
  process.stdout.write('Uploading screenshot to agent...');
  try {
    const fileBuffer = fs.readFileSync(filePath);
    const blob = new Blob([fileBuffer], { type: 'image/png' });
    
    const formData = new FormData();
    formData.append('file', blob, path.basename(filePath));
    formData.append('chat_id', chatId);

    const resp = await fetch(uploadUrl, {
      method: 'POST',
      headers: {
        'Cookie': `g4l_session=${token}`
      },
      body: formData
    });

    if (!resp.ok) {
      console.error(`\nUpload failed (HTTP ${resp.status}): ${await resp.text()}`);
      process.exit(1);
    }

    const resJson = await resp.json();
    console.log(' Done.');
    return resJson.id;
  } catch (err) {
    console.error(`\nError uploading file: ${err.message}`);
    process.exit(1);
  }
}

async function queryAgent(url, token, params) {
  const streamUrl = `${url.replace(/\/$/, '')}/chat/stream`;
  const urlParams = new URLSearchParams(params);

  console.log(`Connecting to Clixen stream with chat_id=${params.chat_id}...`);
  try {
    const response = await fetch(`${streamUrl}?${urlParams.toString()}`, {
      headers: {
        'Cookie': `g4l_session=${token}`
      }
    });

    if (!response.ok) {
      console.error(`Failed to connect to agent stream (HTTP ${response.status})`);
      console.error(await response.text());
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    console.log('\n--- Agent Response ---');
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // Keep partial line in buffer

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('data:')) {
          const dataStr = trimmed.slice(5).trim();
          try {
            const data = JSON.parse(dataStr);
            if (data.token) {
              process.stdout.write(data.token);
            }
            if (data.error) {
              process.stdout.write(`\n[Error: ${data.error}]\n`);
            }
            if (data.done) {
              console.log(`\n\n[Done. Model: ${data.model || 'unknown'}, Intent: ${data.intent || 'unknown'}]`);
              return;
            }
          } catch (e) {
            // Ignore
          }
        }
      }
    }
  } catch (err) {
    console.error(`\nStream connection error: ${err.message}`);
  }
}

async function main() {
  const args = {
    message: 'Describe what is on my screen and help me with it.',
    interactive: false,
    url: 'http://localhost:9234',
    chatId: 'web_ui',
    model: '',
    webSearch: false,
    localAgent: false,
    planMode: false
  };

  // Simple arg parsing
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '-m' || arg === '--message') {
      args.message = argv[++i];
    } else if (arg === '-i' || arg === '--interactive') {
      args.interactive = true;
    } else if (arg === '-u' || arg === '--url') {
      args.url = argv[++i];
    } else if (arg === '-c' || arg === '--chat-id') {
      args.chatId = argv[++i];
    } else if (arg === '--model') {
      args.model = argv[++i];
    } else if (arg === '--web-search') {
      args.webSearch = true;
    } else if (arg === '--local-agent') {
      args.localAgent = true;
    } else if (arg === '--plan-mode') {
      args.planMode = true;
    } else if (arg === '-h' || arg === '--help') {
      console.log(`Usage: node clixen_shot.mjs [options]
Options:
  -m, --message <msg>   Text prompt to send (default: Describe what is on my screen)
  -i, --interactive     Interactive capture (select area/window)
  -u, --url <url>       Clixen server URL (default: http://localhost:9234)
  -c, --chat-id <id>    Chat session ID (default: web_ui)
  --model <model>       Force specific model
  --web-search          Force web search
  --local-agent         Force local agent tool execution
  --plan-mode           Force plan mode`);
      process.exit(0);
    }
  }

  const { email, secret } = getWorkspaceState();
  const token = signSession(email, secret);

  const filePath = captureScreen(args.interactive);
  try {
    const fileId = await uploadScreenshot(args.url, token, filePath, args.chatId);
    
    const params = {
      message: args.message,
      chat_id: args.chatId,
      file_ids: fileId,
      web_search: args.webSearch ? '1' : '0',
      local_agent: args.localAgent ? '1' : '0',
      plan_mode: args.planMode ? '1' : '0',
      model: args.model
    };

    await queryAgent(args.url, token, params);
  } finally {
    try {
      fs.unlinkSync(filePath);
    } catch (e) {}
  }
}

main().catch(console.error);
