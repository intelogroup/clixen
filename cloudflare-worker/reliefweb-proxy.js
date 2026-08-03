// Allowed hosts for the ?download= proxy — blocks open-relay use of this worker.
const DOWNLOAD_ALLOW_HOSTS = ['reliefweb.int', 'www.reliefweb.int'];

function corsHeader(request) {
  const origin = request.headers.get('Origin') || '';
  return (origin === 'http://localhost:9234' || origin.startsWith('http://localhost:'))
    ? { 'Access-Control-Allow-Origin': origin }
    : {};
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const cors = corsHeader(request);

    // PDF download proxy: ?download=<encoded_url> — only reliefweb.int hosts.
    const downloadUrl = url.searchParams.get('download');
    if (downloadUrl) {
      const target = new URL(decodeURIComponent(downloadUrl));
      if (!DOWNLOAD_ALLOW_HOSTS.includes(target.hostname)) {
        return new Response('Not allowed', { status: 403 });
      }
      const response = await fetch(target.toString(), {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
          'Accept': 'application/pdf,application/octet-stream,*/*',
        },
      });
      const contentType = response.headers.get('Content-Type') || 'application/pdf';
      return new Response(response.body, {
        status: response.status,
        headers: {
          'Content-Type': contentType,
          ...cors,
        },
      });
    }

    // RSS feed proxy: ?search=<query>
    const search = url.searchParams.get('search') || '';
    const rssUrl = `https://reliefweb.int/updates/rss.xml?search=${encodeURIComponent(search)}`;
    const response = await fetch(rssUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'application/rss+xml,application/xml,text/xml,*/*',
      },
    });
    return new Response(response.body, {
      status: response.status,
      headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' },
    });
  },
};
