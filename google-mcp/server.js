#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { google } from 'googleapis';

async function getGmail() {
  const credPath = process.env.GMAIL_CREDENTIALS_PATH || '/Users/kalinovdameus/developer/zl_master_board/gmail-mcp/credentials.json';
  const tokenPath = process.env.GMAIL_TOKEN_PATH || '/Users/kalinovdameus/developer/zl_master_board/gmail-mcp/token.json';
  const { readFileSync } = await import('fs');
  const creds = JSON.parse(readFileSync(credPath, 'utf8'));
  const token = JSON.parse(readFileSync(tokenPath, 'utf8'));
  const auth = new google.auth.OAuth2(creds.web.client_id, creds.web.client_secret, 'http://localhost:3333');
  auth.setCredentials(token);
  return google.gmail({ version: 'v1', auth });
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
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const gmail = await getGmail();
  const { name, arguments: args } = request.params;

  if (name === 'list_emails' || name === 'search_emails') {
    const res = await gmail.users.messages.list({
      userId: 'me',
      maxResults: args.maxResults || 10,
      q: args.query || (name === 'list_emails' ? 'in:inbox' : undefined),
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
    const msg = await gmail.users.messages.get({
      userId: 'me',
      id: args.messageId,
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

  return { content: [{ type: 'text', text: `Unknown tool: ${name}` }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
