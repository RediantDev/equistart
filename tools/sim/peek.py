"""peek.py WHO URL WIDTH [selector] — скриншот экрана, опционально элемента."""
import sys; sys.path.insert(0, "/home/claude/sim")
from playwright.sync_api import sync_playwright
from flows import login
from people import Person
who, url, w = sys.argv[1], sys.argv[2], int(sys.argv[3])
sel = sys.argv[4] if len(sys.argv) > 4 else None
full = "--full" in sys.argv
cookies = []
if who != "guest":
    ph, idx = who.split(":")
    b = login("peek", Person(int(idx), ph))
    cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in b.s.cookies]
with sync_playwright() as p:
    br = p.chromium.launch(executable_path="/opt/google/chrome/chrome", args=["--no-sandbox"])
    ctx = br.new_context(viewport={"width": w, "height": 800}); ctx.add_cookies(cookies) if cookies else None
    pg = ctx.new_page(); pg.goto("http://127.0.0.1:8000" + url, wait_until="load")
    out = f"/home/claude/sim/peek_{w}.png"
    if sel and sel != "--full":
        el = pg.locator(sel).first
        el.scroll_into_view_if_needed()
        print(el.bounding_box())
        print(pg.evaluate("(s) => { const e = document.querySelector(s); let p = e, out=[]; while (p) { out.push(p.tagName + '.' + p.className + ' ' + getComputedStyle(p).display + ' ' + Math.round(p.getBoundingClientRect().left) + '..' + Math.round(p.getBoundingClientRect().right)); p = p.parentElement; } return out.slice(0,8); }", sel))
        pg.screenshot(path=out)
    else:
        pg.screenshot(path=out, full_page=full)
    print(out)
