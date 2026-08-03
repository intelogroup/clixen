export default {
  async fetch(request) {
    const url = new URL(request.url);

    // PDF download proxy: ?download=<encoded_url>
    const downloadUrl = url.searchParams.get('download');
    if (downloadUrl) {
      const response = await fetch(decodeURIComponent(downloadUrl), {
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
          'Access-Control-Allow-Origin': '*',
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
