export default {
  async fetch(request) {
    // Narrow CORS: only allow the local Clixen app (localhost:9234) and localhost
    // dev origins, instead of the previous wildcard that let any website scrape
    // through this worker.
    const origin = request.headers.get('Origin') || '';
    const corsHeader = (origin === 'http://localhost:9234' || origin.startsWith('http://localhost:'))
      ? { 'Access-Control-Allow-Origin': origin }
      : {};
    try {
      const url = new URL(request.url);
      const query = url.searchParams.get('q') || '';
      if (!query) {
        return new Response(JSON.stringify({ error: 'Missing ?q= parameter' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json', ...corsHeader },
        });
      }

      const response = await fetch('https://html.duckduckgo.com/html/', {
        method: 'POST',
        body: new URLSearchParams({ q: query }),
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
          Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.5',
          'Content-Type': 'application/x-www-form-urlencoded',
        },
      });

      const html = await response.text();
      const results = [];
      const resultRegex = /<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g;
      const snippetRegex = /<a[^>]*class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g;

      let match;
      const links = [];
      while ((match = resultRegex.exec(html)) !== null) {
        links.push({ url: match[1], title: match[2].replace(/<[^>]+>/g, '').trim() });
      }
      const snippets = [];
      while ((match = snippetRegex.exec(html)) !== null) {
        snippets.push(match[1].replace(/<[^>]+>/g, '').trim());
      }

      for (let i = 0; i < links.length; i++) {
        results.push({
          title: links[i].title,
          url: links[i].url,
          snippet: snippets[i] || '',
        });
      }

      return new Response(JSON.stringify({ query, results, total: results.length }), {
        headers: { 'Content-Type': 'application/json', ...corsHeader },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json', ...corsHeader },
      });
    }
  },
};
