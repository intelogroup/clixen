"""Drives cursor.html in headless Chromium with a mocked Tauri event bridge.
    ../.venv/bin/python test/cursor_overlay.py
Checks the sprite path (amber paw dot lands on the click target) and the fallback path (sprites
blocked -> the white arrow draws instead, with a console warning and no page error)."""
import subprocess, sys, time, urllib.request
from playwright.sync_api import sync_playwright

PORT = 5199
MOCK = """
window.__h = {};
window.__TAURI_INTERNALS__ = {
  transformCallback(cb) { const id = Math.floor(Math.random() * 1e9); window['_' + id] = cb; return id; },
  invoke(cmd, args) { if (cmd === 'plugin:event|listen') window.__h[args.event] = args.handler; return Promise.resolve(0); },
};
window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
"""
TARGET = (600, 400)


def pixel(page, x, y):
    return page.evaluate("([x,y]) => Array.from(document.getElementById('c').getContext('2d').getImageData(x,y,1,1).data)", [x, y])


def amber_near(page, x, y, r=4):
    """Is there an amber (the paw dot) pixel within r px of the target?"""
    return page.evaluate("""([x,y,r]) => { const c = document.getElementById('c').getContext('2d');
      for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) { const d = c.getImageData(x+dx, y+dy, 1, 1).data;
        if (d[3] > 200 && d[0] > 240 && d[1] > 150 && d[1] < 200 && d[2] < 110) return true; } return false; }""", [x, y, r])


def run(browser, block_sprites):
    page = browser.new_page(viewport={"width": 900, "height": 600})
    errs, warns = [], []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: warns.append(m.text) if m.type == "warning" else None)
    if block_sprites:
        page.route("**/sprites/**", lambda r: r.abort())
    page.add_init_script(MOCK)
    page.goto(f"http://localhost:{PORT}/cursor.html")
    page.wait_for_function("window.__h['cursor-fly']", timeout=5000)
    page.wait_for_timeout(500)  # sprites load
    page.evaluate("([x,y]) => window['_' + window.__h['cursor-fly']]({event:'cursor-fly', id:1, payload:{from:[100,100], to:[x,y], click:true}})", list(TARGET))
    page.wait_for_timeout(2600)  # flight, press, then parked
    px = [amber_near(page, *TARGET), pixel(page, TARGET[0] + 1, TARGET[1] + 8)]
    page.close()
    return px, errs, warns


def main():
    srv = subprocess.Popen(["npx", "vite", "--port", str(PORT), "--strictPort"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://localhost:{PORT}/cursor.html", timeout=1); break
            except Exception:
                time.sleep(0.3)
        ok = True
        with sync_playwright() as p:
            b = p.chromium.launch()
            (dot, _), errs, _w = run(b, block_sprites=False)
            good = dot and not errs
            print("sprites  paw dot at target:", dot, "errors:", errs, "->", "PASS" if good else "FAIL"); ok &= good
            (_amber, below), errs, warns = run(b, block_sprites=True)
            tip = below
            good = below[3] > 200 and below[0] > 200 and below[1] > 200 and not errs and any("sprites unavailable" in w for w in warns)
            print("fallback arrow at target:", tip, below, "errors:", errs, "warned:", any("sprites unavailable" in w for w in warns), "->", "PASS" if good else "FAIL"); ok &= good
            b.close()
        sys.exit(0 if ok else 1)
    finally:
        srv.terminate()


main()
