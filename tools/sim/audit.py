"""Аудит вёрстки: обходит все доступные по ссылкам экраны каждой роли и меряет их на 1440 / 768 / 390 / 360.
Ищет: горизонтальную прокрутку страницы, вылезающие элементы, мелкие кнопки на телефоне, поля с шрифтом < 16px
(iOS увеличивает страницу при фокусе), текст мельче 12px, ошибки консоли и сломанные запросы."""
import re, sys, json, time
from collections import defaultdict
from urllib.parse import urlparse
sys.path.insert(0, "/home/claude/sim")
from playwright.sync_api import sync_playwright
from browser import Browser, BASE
from flows import login
from people import Person

ONLY = sys.argv[1:]  # можно ограничить ролями
OUT = "/home/claude/sim/audit"
import os; os.makedirs(OUT, exist_ok=True)
WIDTHS = [1440, 768, 390, 360]
SKIP = re.compile(r"(^tel:|^mailto:|logout|\.xlsx|\.pdf|\.csv|/manage/|/static/|/media/|/clubs/file/|/files?/|#|/health/|export|download|print=|/sheet\.)")
ROLES = {
    "гость": None,
    "организатор S1": ("+79261001001", 1001),
    "конюшня S1": ("+79261001003", 1003),
    "судья на букве S3": ("+79263001002", 3002),
    "судья-подсчёт S3": ("+79263001010", 3010),
    "тренер, большая заявка": ("+79260003106", 3106),
    "спортсмен S3": ("+79260004100", 4100),
    "клуб": ("+79266001001", 6001),
    "модератор": ("+79160000000", 0),
}

JS = r"""() => {
 const W = window.innerWidth, out = [];
 const isClipped = el => { let p = el.parentElement; while (p && p !== document.body) { const s = getComputedStyle(p); if (['auto','scroll','hidden','clip'].includes(s.overflowX)) return true; p = p.parentElement; } return false; };
 const name = el => (el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/).slice(0,2).join('.') : '')).slice(0, 60);
 for (const el of document.querySelectorAll('body *')) {
   const r = el.getBoundingClientRect();
   if (r.width === 0 || r.height === 0) continue;
   if (el.closest('.skip-link, .visually-hidden')) continue;
   if ((r.right > W + 1 || r.left < -1) && !isClipped(el)) out.push(name(el) + ' →' + Math.round(r.right));
 }
 const mobile = W < 500;
 const smallTap = [], tinyText = [], zoomInputs = [], clippedText = [];
 if (mobile) {
   for (const e of document.querySelectorAll('button, a.btn, input[type=submit], select, input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea, .topnav__link, .tabs a, .chip[href], a.tag')) {
     const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
     const st = getComputedStyle(e); if (st.visibility === 'hidden') continue;
     if (r.height < 36) smallTap.push(name(e) + ' ' + Math.round(r.height) + 'px «' + (e.innerText || e.value || '').trim().slice(0, 20) + '»');
     if (['INPUT','SELECT','TEXTAREA'].includes(e.tagName) && parseFloat(st.fontSize) < 16) zoomInputs.push(name(e) + ' ' + st.fontSize);
   }
 }
 for (const e of document.querySelectorAll('p, span, a, td, th, li, label, div, small, button')) {
   if (!e.childNodes.length || ![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
   const st = getComputedStyle(e); const r = e.getBoundingClientRect(); if (!r.width || e.closest('.visually-hidden, .bell__count')) continue;
   if (parseFloat(st.fontSize) < 12) tinyText.push(name(e) + ' ' + st.fontSize);
   if (e.scrollWidth > e.clientWidth + 2 && ['hidden','clip'].includes(st.overflowX) && st.textOverflow !== 'ellipsis' && e.clientWidth > 0) clippedText.push(name(e) + ' «' + e.innerText.trim().slice(0, 30) + '»');
 }
 const imgs = [...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.loading !== 'lazy').map(i => i.src.slice(-50));
 const scrollers = [...document.querySelectorAll('*')].filter(e => { const s = getComputedStyle(e); return ['auto','scroll'].includes(s.overflowX) && e.scrollWidth > e.clientWidth + 2; }).map(name).slice(0, 5);
 return {docW: document.documentElement.scrollWidth, W, H: document.documentElement.scrollHeight, nodes: document.getElementsByTagName('*').length,
   overflow: [...new Set(out)].slice(0, 10), smallTap: [...new Set(smallTap)].slice(0, 12), zoomInputs: [...new Set(zoomInputs)].slice(0, 6),
   tinyText: [...new Set(tinyText)].slice(0, 6), clippedText: [...new Set(clippedText)].slice(0, 6), brokenImgs: imgs.slice(0, 4), scrollers,
   title: document.title};
}"""


def pattern(path):
    return re.sub(r"\d+", "{n}", path)


def discover(b, max_pages=140, per_pattern=2):
    start = "/cabinet/" if b else "/"
    seen, pats, queue = set(), defaultdict(int), [start, "/", "/clubs/"]
    urls = []
    while queue and len(urls) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        p = bw.get(url, note="audit")
        if p.status >= 400:
            urls.append((url, p.status))
            continue
        if not p.soup:
            continue
        urls.append((url, p.status))
        for _, href in p.links():
            u = urlparse(href)
            if u.netloc and u.netloc not in BASE:
                continue
            q = u.query
            # оставляем вкладки и фильтры, отбрасываем прочие параметры
            keep = "&".join(x for x in q.split("&") if x.split("=")[0] in ("tab", "day", "state", "show", "scope", "custom", "past", "club"))
            key = u.path + ("?" + keep if keep else "")
            if SKIP.search(href) or key in seen:
                continue
            pk = pattern(u.path) + ("?" + re.sub(r"=[^&]*", "", keep) if keep else "")
            if pats[pk] >= per_pattern:
                continue
            pats[pk] += 1
            queue.append(key)
    return urls


results = []
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path="/opt/google/chrome/chrome", args=["--no-sandbox"])
    for role, who in ROLES.items():
        if ONLY and role not in ONLY:
            continue
        if who:
            bw = login("audit", Person(who[1], who[0]))
            cookies = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in bw.s.cookies]
        else:
            bw = Browser("audit", "гость")
            cookies = []
        urls = discover(who)
        print(role, "pages", len(urls), flush=True)
        for w in WIDTHS:
            ctx = br.new_context(viewport={"width": w, "height": 900 if w > 500 else 780}, device_scale_factor=1)
            if cookies:
                ctx.add_cookies(cookies)
            pg = ctx.new_page()
            errs = []
            pg.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
            pg.on("response", lambda r: errs.append(f"{r.status} {urlparse(r.url).path}") if r.status >= 400 and "favicon" not in r.url else None)
            for url, st in urls:
                errs.clear()
                if st >= 400:
                    if w == 1440:
                        results.append(dict(role=role, url=url, width=w, status=st))
                    continue
                try:
                    resp = pg.goto(BASE + url, wait_until="load", timeout=60000)
                    m = pg.evaluate(JS)
                except Exception as e:
                    results.append(dict(role=role, url=url, width=w, error=str(e)[:200])); continue
                m.update(role=role, url=url, width=w, status=resp.status if resp else None, console=list(errs)[:5])
                results.append(m)
            ctx.close()
            print(" ", role, w, "done", flush=True)
        json.dump(results, open(f"{OUT}/results.json", "w"), ensure_ascii=False, indent=0)
    br.close()
print("AUDIT-DONE", len(results))
