#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { google } from 'googleapis';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function getGoogleAuth() {
  const credPath = process.env.GMAIL_CREDENTIALS_PATH || path.join(__dirname, 'credentials.json');
  const tokenPath = process.env.GMAIL_TOKEN_PATH || path.join(__dirname, 'token.json');
  const { readFileSync } = await import('fs');
  const creds = JSON.parse(readFileSync(credPath, 'utf8'));
  const token = JSON.parse(readFileSync(tokenPath, 'utf8'));
  const auth = new google.auth.OAuth2(creds.web.client_id, creds.web.client_secret, 'http://localhost:3333');
  auth.setCredentials(token);
  return auth;
}

async function getGmail() {
  return google.gmail({ version: 'v1', auth: await getGoogleAuth() });
}

async function getTasks() {
  return google.tasks({ version: 'v1', auth: await getGoogleAuth() });
}

const server = new Server(
  { name: 'gmail', version: '1.0.0' },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'list_emails',
      description: 'List recent emails from Gmail inbox',
      inputSchema: {
        type: 'object',
        properties: {
          maxResults: { type: 'number', description: 'Max emails to return (default 10)', default: 10 },
          query: { type: 'string', description: 'Gmail search query e.g. "is:unread", "from:someone@example.com"' },
        },
      },
    },
    {
      name: 'read_email',
      description: 'Read the full content of an email by message ID',
      inputSchema: {
        type: 'object',
        properties: {
          messageId: { type: 'string', description: 'Gmail message ID' },
        },
        required: ['messageId'],
      },
    },
    {
      name: 'search_emails',
      description: 'Search emails using Gmail query syntax',
      inputSchema: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Gmail search query' },
          maxResults: { type: 'number', default: 20 },
        },
        required: ['query'],
      },
    },
    {
      name: 'list_task_lists',
      description: 'List Google Tasks lists available to the authenticated account',
      inputSchema: {
        type: 'object',
        properties: {},
      },
    },
    {
      name: 'list_tasks',
      description: 'List tasks in a Google Tasks list; defaults to the default list',
      inputSchema: {
        type: 'object',
        properties: {
          taskListId: { type: 'string', description: 'Google Tasks list ID (default: @default)' },
          showCompleted: { type: 'boolean', description: 'Include completed tasks (default: false)', default: false },
        },
      },
    },
    {
      name: 'complete_all_tasks',
      description: 'Mark every incomplete task in a Google Tasks list as completed; defaults to the default list',
      inputSchema: {
        type: 'object',
        properties: {
          taskListId: { type: 'string', description: 'Google Tasks list ID (default: @default)' },
        },
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const params = args || {};

  if (name === 'list_emails' || name === 'search_emails') {
    const gmail = await getGmail();
    const res = await gmail.users.messages.list({
      userId: 'me',
      maxResults: params.maxResults || 10,
      q: params.query || (name === 'list_emails' ? 'in:inbox' : undefined),
    });

    const messages = res.data.messages || [];
    if (!messages.length) return { content: [{ type: 'text', text: 'No messages found.' }] };

    const details = await Promise.all(
      messages.map(async (m) => {
        const msg = await gmail.users.messages.get({
          userId: 'me',
          id: m.id,
          format: 'metadata',
          metadataHeaders: ['Subject', 'From', 'Date'],
        });
        const headers = msg.data.payload.headers;
        const get = (name) => headers.find((h) => h.name === name)?.value || '';
        return `ID: ${m.id}\nFrom: ${get('From')}\nDate: ${get('Date')}\nSubject: ${get('Subject')}`;
      })
    );

    return { content: [{ type: 'text', text: details.join('\n\n---\n\n') }] };
  }

  if (name === 'read_email') {
    const gmail = await getGmail();
    const msg = await gmail.users.messages.get({
      userId: 'me',
      id: params.messageId,
      format: 'full',
    });

    const headers = msg.data.payload.headers;
    const get = (name) => headers.find((h) => h.name === name)?.value || '';

    function extractBody(payload) {
      if (payload.body?.data) {
        return Buffer.from(payload.body.data, 'base64').toString('utf8');
      }
      if (payload.parts) {
        for (const part of payload.parts) {
          if (part.mimeType === 'text/plain' && part.body?.data) {
            return Buffer.from(part.body.data, 'base64').toString('utf8');
          }
        }
        for (const part of payload.parts) {
          const text = extractBody(part);
          if (text) return text;
        }
      }
      return '(no plain text body)';
    }

    const body = extractBody(msg.data.payload);

    return {
      content: [{
        type: 'text',
        text: `From: ${get('From')}\nDate: ${get('Date')}\nSubject: ${get('Subject')}\n\n${body}`,
      }],
    };
  }

  if (name === 'list_task_lists') {
    const tasks = await getTasks();
    const lists = [];
    let pageToken;
    do {
      const res = await tasks.tasklists.list({ maxResults: 100, pageToken });
      lists.push(...(res.data.items || []));
      pageToken = res.data.nextPageToken;
    } while (pageToken);

    if (!lists.length) return { content: [{ type: 'text', text: 'No Google Tasks lists found.' }] };
    return {
      content: [{
        type: 'text',
        text: lists.map((list) => `${list.title} (ID: ${list.id})`).join('\n'),
      }],
    };
  }

  if (name === 'list_tasks' || name === 'complete_all_tasks') {
    const tasks = await getTasks();
    const taskListId = params.taskListId || '@default';
    const items = [];
    let pageToken;
    do {
      const res = await tasks.tasks.list({
        tasklist: taskListId,
        maxResults: 100,
        pageToken,
        showCompleted: name === 'list_tasks' ? Boolean(params.showCompleted) : false,
        showHidden: true,
      });
      items.push(...(res.data.items || []));
      pageToken = res.data.nextPageToken;
    } while (pageToken);

    if (name === 'list_tasks') {
      if (!items.length) return { content: [{ type: 'text', text: 'No tasks found.' }] };
      return {
        content: [{
          type: 'text',
          text: items.map((task) => {
            const status = task.status === 'completed' ? 'completed' : 'incomplete';
            return `${task.title || '(untitled)'} — ${status} (ID: ${task.id})`;
          }).join('\n'),
        }],
      };
    }

    let completed = 0;
    for (const task of items) {
      if (task.status !== 'completed') {
        await tasks.tasks.patch({
          tasklist: taskListId,
          task: task.id,
          requestBody: { status: 'completed' },
        });
        completed += 1;
      }
    }
    return {
      content: [{
        type: 'text',
        text: completed ? `Marked ${completed} task${completed === 1 ? '' : 's'} complete.` : 'No incomplete tasks found.',
      }],
    };
  }

  return { content: [{ type: 'text', text: `Unknown tool: ${name}` }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
