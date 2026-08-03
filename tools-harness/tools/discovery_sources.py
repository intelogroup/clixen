import time
import requests
from urllib.parse import quote

_UA = {"User-Agent": "clixen-agent/1.0 (mailto:jimkalinov@gmail.com)"}
_TIMEOUT = 12
_MAILTO = "jimkalinov@gmail.com"


def _get_retry(url: str, headers: dict, retries: int = 3) -> requests.Response:
    """GET with exponential backoff on 429/5xx. Crossref/OpenAlex throttle
    bursts; Retry-After is honored when present (per Crossref API etiquette)."""
    delay = 1.0
    for attempt in range(retries):
        r = requests.get(url, timeout=_TIMEOUT, headers=headers)
        if r.status_code not in (429, 500, 502, 503, 504):
            return r
        wait = r.headers.get("Retry-After")
        try:
            time.sleep(float(wait) if wait else delay)
        except (TypeError, ValueError):
            time.sleep(delay)
        delay *= 2
    return r

def search_wikidata(query: str, limit: int = 5) -> str:
    """Search Wikidata for entities matching a query."""
    url = (
        "https://www.wikidata.org/w/api.php"
        f"?action=wbsearchentities&search={quote(query)}&language=en&format=json&limit={limit}"
    )
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_UA)
        r.raise_for_status()
        items = r.json().get("search", [])
        if not items:
            return f"No Wikidata entities found for '{query}'"
        
        lines = []
        for e in items:
            lines.append(f"- ID: {e['id']} | Label: {e['label']} | Description: {e.get('description', 'No description available')}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error searching Wikidata: {e}]"

def search_openalex(query: str, limit: int = 5) -> str:
    """Search OpenAlex for academic works/articles matching a query."""
    url = (
        "https://api.openalex.org/works"
        f"?search={quote(query)}&per-page={limit}&select=title,doi,publication_year,open_access&mailto={_MAILTO}"
    )
    try:
        r = _get_retry(url, _UA)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return f"No academic works found on OpenAlex for '{query}'"
        
        lines = []
        for w in results:
            oa = w.get("open_access", {}).get("is_oa", False)
            lines.append(f"- Title: {w.get('title', '?')} | Year: {w.get('publication_year', '?')} | OA: {oa} | DOI: {w.get('doi', '?')}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error searching OpenAlex: {e}]"

def search_crossref(query: str, limit: int = 5) -> str:
    """Search Crossref for metadata of publications matching a query."""
    url = f"https://api.crossref.org/works?query={quote(query)}&rows={limit}&mailto={_MAILTO}"
    try:
        r = _get_retry(url, _UA)
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        if not items:
            return f"No works found on Crossref for '{query}'"
        
        lines = []
        for w in items:
            title = (w.get("title") or ["?"])[0]
            year = (w.get("published", {}).get("date-parts") or [[None]])[0][0]
            doi = w.get("DOI", "?")
            lines.append(f"- Title: {title} | Year: {year} | DOI: {doi}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error searching Crossref: {e}]"

def search_opencorporates(query: str) -> str:
    """Search OpenCorporates for corporate entity registries (HTML page extraction)."""
    url = f"https://opencorporates.com/companies?q={quote(query)}&utf8=%E2%9C%93"
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_UA)
        r.raise_for_status()
        text = r.text
        if "No companies found" in text:
            return f"No companies found on OpenCorporates for '{query}'"
        
        # Simple parser to extract company names and status from HTML
        # In a real tool, we might regex-match or BeautifulSoup-parse
        import re
        # Find matches like: <a class="company_search_result" ...>Company Name</a>
        matches = re.findall(r'class="company_search_result[^"]*"\s+href="([^"]+)">([^<]+)</a>', text)
        if not matches:
            if query in text and len(text) > 1000:
                return f"OpenCorporates HTML search returned results page for '{query}', length: {len(text)} chars."
            return f"Failed to parse company search results from OpenCorporates HTML page for '{query}'"
        
        lines = []
        for link, name in matches[:10]:
            lines.append(f"- Name: {name.strip()} | Link: https://opencorporates.com{link}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error searching OpenCorporates: {e}]"

def search_sec_edgar(query: str, limit: int = 5) -> str:
    """Search SEC EDGAR database for 10-K financial filings matching a query."""
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q={quote(query)}&forms=10-K&dateRange=custom&startdt=2023-01-01&hits.hits._source=file_date,entity_name,form_type"
    )
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_UA)
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
        if not hits:
            # Fallback search without date constraint
            url_fallback = f"https://efts.sec.gov/LATEST/search-index?q={quote(query)}&forms=10-K"
            r_fallback = requests.get(url_fallback, timeout=_TIMEOUT, headers=_UA)
            r_fallback.raise_for_status()
            hits = r_fallback.json().get("hits", {}).get("hits", [])
            
        if not hits:
            return f"No SEC filings found for '{query}'"
            
        lines = []
        for h in hits[:limit]:
            src = h.get("_source", h)
            entity = (src.get("display_names") or ["?"])[0]
            form = (src.get("root_forms") or [src.get("form", "?")])[0]
            date = src.get("file_date", "?")
            biz_loc = (src.get("biz_locations") or ["?"])[0]
            lines.append(f"- Entity: {entity} | Form: {form} | Date: {date} | Biz Location: {biz_loc}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error searching SEC EDGAR: {e}]"

def geocode_address(address: str, limit: int = 3) -> str:
    """Geocode an address to latitude and longitude coordinates using OpenStreetMap Nominatim."""
    url = (
        "https://nominatim.openstreetmap.org/search"
        f"?q={quote(address)}&format=json&limit={limit}&addressdetails=1"
    )
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_UA)
        r.raise_for_status()
        results = r.json()
        if not results:
            return f"No location coordinates found for '{address}'"
        
        lines = []
        for p in results:
            addr = p.get("address", {})
            lines.append(f"- Display Name: {p.get('display_name', '?')} | Lat: {p['lat']} | Lon: {p['lon']} | Country: {addr.get('country', '?')}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error geocoding address: {e}]"

def search_wikipedia(query: str, limit: int = 3, sentences: int = 3) -> str:
    """Search Wikipedia articles and return extracts matching a query."""
    # ponytail: single Wikipedia API call, no caching. Add cache if latency matters.
    search_url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&list=search&srsearch={quote(query)}&srlimit={limit}&format=json"
    )
    try:
        sr = requests.get(search_url, timeout=_TIMEOUT, headers=_UA)
        sr.raise_for_status()
        results = sr.json().get("query", {}).get("search", [])
        if not results:
            return f"No Wikipedia articles found for '{query}'"

        # Fetch extracts for top results
        titles = "|".join(r["title"] for r in results)
        extract_url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&prop=extracts&exintro&explaintext&exsentences={sentences}"
            f"&titles={quote(titles)}&format=json"
        )
        er = requests.get(extract_url, timeout=_TIMEOUT, headers=_UA)
        er.raise_for_status()
        pages = er.json().get("query", {}).get("pages", {})

        lines = []
        for r in results:
            page_id = str(r.get("pageid", ""))
            page = pages.get(page_id, {})
            extract = page.get("extract", "")
            snippet = extract[:500] + "..." if len(extract) > 500 else extract
            lines.append(
                f"- {r['title']} | URL: https://en.wikipedia.org/wiki/{quote(r['title'].replace(' ', '_'))}\n"
                f"  {snippet}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        return f"[Error searching Wikipedia: {e}]"


def search_gdelt_news(query: str, limit: int = 5) -> str:
    """Search global news articles matching a query using the GDELT DOC API v2."""
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote(query)}&mode=artlist&maxrecords={limit}&format=json"
    )
    try:
        r = requests.get(url, timeout=15, headers=_UA)  # 15s timeout since GDELT is slow
        if r.status_code == 429:
            return f"[GDELT Rate Limit] API is functional but temporarily throttled."
        r.raise_for_status()
        articles = r.json().get("articles", [])
        if not articles:
            return f"No news articles found on GDELT for '{query}'"
            
        lines = []
        for a in articles:
            lines.append(f"- Title: {a.get('title', '?')} | URL: {a.get('url', '?')} | Date Seen: {a.get('seendate', '?')}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Error searching GDELT News: {e}]"


# =========================================================================
# LLM Tool Schemas
# =========================================================================

WIKIDATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_wikidata",
        "description": "Search Wikidata for semantic entities, descriptions, and IDs matching a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Entity name or topic keyword (e.g. 'Albert Einstein', 'Quantum computing')"},
                "limit": {"type": "integer", "description": "Max results to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
}

OPENALEX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_openalex",
        "description": "Search OpenAlex for academic literature, publications, open-access status, and metadata.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords (e.g. 'CRISPR gene editing')"},
                "limit": {"type": "integer", "description": "Max results to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
}

CROSSREF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_crossref",
        "description": "Search Crossref metadata for publication dates, titles, and DOIs matching a search query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query keywords"},
                "limit": {"type": "integer", "description": "Max results to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
}

OPENCORPORATES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_opencorporates",
        "description": "Search OpenCorporates database for company registration names and URLs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Company name keyword (e.g. 'Apple Inc')"},
            },
            "required": ["query"],
        },
    },
}

SEC_EDGAR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_sec_edgar",
        "description": "Search SEC EDGAR database for 10-K financial filings, filing dates, and company locations.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Company name or ticker query"},
                "limit": {"type": "integer", "description": "Max results to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
}

GEOCODE_ADDRESS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "geocode_address",
        "description": "Geocode an address or landmark name to obtain latitude, longitude, and country using Nominatim.",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Address or landmark query (e.g. 'Eiffel Tower Paris', '1600 Amphitheatre Pkwy Mountain View')"},
                "limit": {"type": "integer", "description": "Max results to return (default 3)", "default": 3},
            },
            "required": ["address"],
        },
    },
}

WIKIPEDIA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_wikipedia",
        "description": "Search Wikipedia articles and return extracts/summaries with links matching a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query or topic (e.g. 'Python programming language', 'Battle of Waterloo')"},
                "limit": {"type": "integer", "description": "Max articles to return (default 3)", "default": 3},
                "sentences": {"type": "integer", "description": "Sentences per article extract (default 3)", "default": 3},
            },
            "required": ["query"],
        },
    },
}

GDELT_NEWS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_gdelt_news",
        "description": "Search GDELT global news database for recent articles, publication links, and dates matching a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords or search topic"},
                "limit": {"type": "integer", "description": "Max articles to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
}
