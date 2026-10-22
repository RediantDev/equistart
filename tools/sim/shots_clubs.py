import json, sys, os, time
sys.path.insert(0, "/home/claude/sim")
from playwright.sync_api import sync_playwright
from flows import login
from people import Person
exec(open("/home/claude/sim/shots.py").read().split("def cookies_for")[0].split("META = q(")[0].replace('OUT = "/home/claude/sim/shots"', 'OUT = "/home/claude/sim/shots_clubs"'))
os.makedirs(OUT, exist_ok=True)
JS = open("/home/claude/sim/shots.py").read().split('JS = """')[1].split('"""')[0]
m = json.load(open("/home/claude/sim/s6_meta.json"))
C = m["clubs"]
sun = [v for k, v in C.items() if "Солнечная" in k][0]
north = [v for k, v in C.items() if "Северное" in k][0]
SCREENS = [("Каталог клубов (гость)", "/clubs/", None), ("Страница клуба с фото, турниром и новостями", f"/clubs/{sun}/", None),
           ("Клуб с очень длинным названием и адресом", f"/clubs/{north}/", None), ("Календарь, фильтр по клубу", f"/?club={sun}", None),
           ("Турнир со ссылкой на клуб", f"/t/{m['t']}/", None),
           ("Мой клуб (кабинет клуба)", "/my/club/", ("+79266001001", 6001)), ("Кабинет клуба", "/cabinet/", ("+79266001001", 6001)),
           ("Мои соревнования (клуб-конюшня)", "/organizer/", ("+79266001001", 6001)),
           ("Новость: форма", "/my/club/news/new/", ("+79266001001", 6001)),
           ("Модерация: клубы", "/moderation/clubs/", ("+79160000000", 0)), ("Модерация: клуб", f"/moderation/clubs/{sun}/", ("+79160000000", 0))]
res = []
with sync_playwright() as p:
    br = p.chromium.launch(executable_path="/opt/google/chrome/chrome", args=["--no-sandbox"])
    cache = {}
    for name, path, who in SCREENS:
        cookies = None
        if who:
            if who not in cache:
                b = login("shots", Person(who[1], who[0]))
                cache[who] = [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in b.s.cookies]
            cookies = cache[who]
        for w in [1440, 390, 360]:
            ctx = br.new_context(viewport={"width": w, "height": 900 if w > 400 else 800})
            if cookies: ctx.add_cookies(cookies)
            pg = ctx.new_page(); pg.goto("http://127.0.0.1:8000" + path, wait_until="load")
            mm = pg.evaluate(JS); mm.update(name=name, width=w, url=pg.url)
            fn = f"{OUT}/{len(res):02d}_{w}.png"; pg.screenshot(path=fn, full_page=True); mm["shot"] = fn
            res.append(mm); print(name, w, mm["docW"], mm["overflow"][:3], mm["small_tap"], flush=True); ctx.close()
    br.close()
json.dump(res, open(f"{OUT}/metrics.json", "w"), ensure_ascii=False, indent=1)
