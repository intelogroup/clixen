"""One-off crawler: docs.blender.org manual + api (current) -> plain text files,
then index into LanceDB via tools.semantic_files.index_directory.
ponytail: throwaway script, no retries/backoff tuning beyond a flat sleep.
"""
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOTS = [
    "https://docs.blender.org/manual/en/latest/",
    "https://docs.blender.org/api/current/",
]
OUT_DIR = Path(__file__).resolve().parent.parent / "blender_docs_text"
MAX_PAGES = 4000
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("div", attrs={"role": "main"}) or soup.find("main") or soup.find("body")
    if not main:
        return ""
    for tag in main(["script", "style", "nav"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = main.get_text("\n", strip=True)
    return f"{title}\n\n{text}" if title else text


def path_for(url: str) -> Path:
    parsed = urlparse(url)
    rel = parsed.path.strip("/").replace("/", "__") or "index"
    if not rel.endswith(".txt"):
        rel += ".txt"
    return OUT_DIR / rel


def crawl():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seen = set()
    queue = list(ROOTS)
    saved = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)

        while queue and saved < MAX_PAGES:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if not any(url.startswith(r) for r in ROOTS):
                continue
            if "#" in url:
                url = url.split("#")[0]
            try:
                resp = page.goto(url, timeout=20000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"skip {url}: {e}")
                continue
            if resp is None or resp.status != 200:
                continue
            # let Cloudflare's JS challenge (if any) settle
            page.wait_for_timeout(300)
            html = page.content()

            text = clean_text(html)
            if text.strip():
                out_fp = path_for(url)
                out_fp.write_text(text, encoding="utf-8")
                saved += 1
                if saved % 50 == 0:
                    print(f"saved {saved} pages, queue={len(queue)}")

            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=True):
                href = urljoin(url, a["href"]).split("#")[0]
                if href.startswith(tuple(ROOTS)) and href not in seen and href.endswith((".html", "/")):
                    queue.append(href)

        browser.close()

    print(f"done: {saved} pages saved to {OUT_DIR}")


if __name__ == "__main__":
    crawl()
