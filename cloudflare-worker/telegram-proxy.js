/**
 * Cloudflare Worker — Telegram API reverse proxy
 * Deploy at: https://telegram-api-proxy.jimkalinov.workers.dev/
 *
 * Forwards all requests to api.telegram.org, bypassing network-level blocks.
 * Usage: replace https://api.telegram.org with this worker URL in your bot config.
 */
export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = new URL(request.url);
    target.hostname = 'api.telegram.org';
    target.protocol = 'https:';
    target.port = '';

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
