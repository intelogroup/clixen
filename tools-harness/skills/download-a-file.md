# Downloading a file a page offers

Use this when the user says "download", "get me the PDF", "save that file".

## The trap

Most modern sites render from a JSON API, so the download link is **not in the
HTML**. `read_page_text` shows the rendered page, which may not even mention the
file, and `scrapling_fetch_and_extract('a[href*=".pdf"]')` returns nothing because
the anchor is created by JavaScript after load.

Clicking the button visually is the slowest and least reliable route: the button
opens a viewer instead of saving, `click_text` can hit the wrong element after a
scroll, and you still have no file on disk.

## The order that works (fastest first)

**0. If the extension is connected, ask the tab itself: `page_probe(kind="links")`.**
It returns the downloadable URLs already in the page — absolute, de-duplicated, with
their link text, and `match="..."` narrows it. Hand one straight to
`workspace_download` and you are done in two calls with a byte count.

Measured, on a served page with three anchors: `page_probe(kind="links", match="no_44")`
returned `http://127.0.0.1:54321/igm/IGM_vol_03_no_44.pdf`, and `workspace_download`
saved 20,009 bytes at mode 0600 — **no Save As dialog anywhere**. That dialog is where
this task died twice: Chrome opens it, nothing lands in the workspace, and the agent
cannot report a path it did not choose.

1. **`scrapling_api_capture(url, contains="<a distinctive value>")`**
   - `url_pattern` should match the *data* endpoint, not the file: `articles`,
     `api`, `json`, `search`, `list`. Do not use `pdf|download` — the response is
     usually named after the resource, not after the file type.
   - `contains` is what pulls one row out of a long list: a date (`2025-01`), an
     id, or the title. Without it you get `records: 53` and a sample of the newest
     rows, which is often the wrong end of the list.
   - Read the reply's `sample[0]` and find the `pdf_url` / `file_url` /
     `download_url` field. Also check `fields` — it lists every key available.

2. **`workspace_download(url, path)`** with the URL you found.
   - It returns the byte count and the content type. A real PDF reports
     `application/pdf` and megabytes; `text/html` at 30KB means you downloaded a
     login wall or an error page, not the document.

3. **Verify**: `workspace_read` on a PDF returns a binary refusal — that is
   expected and is not a failure. Use `workspace_list` to confirm the size, or
   `analyze_image` / `ocr_image` on a screenshot if you must confirm the content.
   Say the byte count in your reply; "downloaded" with no size is not evidence.

## When there is no API

If `api_capture` returns `no matching network responses`, the page really does
serve its links in the HTML: `scrapling_fetch_and_extract(selector='a[href$=".pdf"]', extract='attrs')`
gets them. If that is also empty, the link needs a session — pass `profile="<site>"`
so cookies survive, then retry.

## Do not

- Do not drive Chrome's download UI when a URL exists; it saves to `~/Downloads`,
  not to the workspace, and you cannot report a path you did not choose.
- Do not report success without a byte count.
- Do not retry the same failing selector; change the layer instead (screen → API →
  HTML → profile).
