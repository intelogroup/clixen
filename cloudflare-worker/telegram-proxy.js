/**
 * Cloudflare Worker — Telegram API reverse proxy
 * Deploy at: https://telegram-api-proxy.jimkalinov.workers.dev/
 *
 * Forwards only Telegram *bot* API calls (path must start with /bot<token>/) to
 * api.telegram.org, bypassing network-level blocks. Open-relay abuse is closed
 * off: requests with any other path get 404. Optionally require the token to
 * match a `TELEGRAM_BOT_TOKEN` environment/secret binding (recommended) —
 * set it in the Workers dashboard if you want to bind this proxy to one bot.
 *
 * Usage: replace https://api.telegram.org with this worker URL in your bot config.
 */
const BOT_TOKEN = (typeof TELEGRAM_BOT_TOKEN !== 'undefined' && TELEGRAM_BOT_TOKEN) || '';

export default {
  async fetch(request) {
    const url = new URL(request.url);

    const match = url.pathname.match(/^\/bot([^/]+)(\/.*)?$/);
    if (!match) {
      return new Response('Not found', { status: 404 });
    }
    const [, token, rest] = match;
    if (BOT_TOKEN && token !== BOT_TOKEN) {
      return new Response('Not found', { status: 404 });
    }

    const target = new URL(`https://api.telegram.org/bot${token}${rest || '/'}${url.search}`);
    const init = {
      method: request.method,
      headers: request.headers,
    };
    if (!['GET', 'HEAD'].includes(request.method)) {
      init.body = request.body;
    }

    return fetch(target.toString(), init);
  },
};
