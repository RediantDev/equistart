"""Скриншоты и метрики вёрстки ключевых экранов на 1440 / 390 / 360 при полной нагрузке данных."""
import json, sys, os, time
sys.path.insert(0, "/home/claude/sim")
from playwright.sync_api import sync_playwright
from browser import Browser
from flows import login
from people import Person
from orm import q

OUT = "/home/claude/sim/shots"
os.makedirs(OUT, exist_ok=True)
WIDTHS = [1440, 390, 360]

META = q("""
from django.db.models import Count
t1 = 3; t3 = 5; t4 = 2
big = ProgramItem.objects.filter(tournament_id=t1).annotate(n=Count('slots')).order_by('-n').first()
d3 = ProgramItem.objects.filter(tournament_id=t3).annotate(n=Count('slots')).order_by('-n').first()
app = Application.objects.filter(tournament_id=t1).annotate(n=Count('entries')).order_by('-n').first()
e4 = Entry.objects.filter(tournament_id=t4, result__isnull=False).first()
e3 = Entry.objects.filter(program=d3, result__isnull=False).first()
out = dict(big=big.id, big_n=big.n, d3=d3.id, d3_n=d3.n, app=app.id, app_n=app.n, app_user=app.submitted_by.phone,
           e4=e4.id, e4_user=e4.application.submitted_by.phone, e3=e3.id, e3_user=e3.application.submitted_by.phone,
           s5=7, s5_d=ProgramItem.objects.filter(tournament_id=7, discipline='dressage').first().id)
""")
print(META)

JS = """() => {
 const W = window.innerWidth, out = [];
 for (const el of document.querySelectorAll('body *')) {
   const r = el.getBoundingClientRect();
   if (r.width === 0 || r.right <= W + 1) continue;
   let p = el.parentElement, clipped = false;
   while (p) { const s = getComputedStyle(p); if (['auto','scroll','hidden'].includes(s.overflowX)) { clipped = true; break; } p = p.parentElement; }
   if (!clipped) out.push((el.tagName + '.' + (el.className||'').toString().split(' ')[0]).slice(0,40) + ' →' + Math.round(r.right));
 }
 const scrollers = [...document.querySelectorAll('*')].filter(e => { const s = getComputedStyle(e); return ['auto','scroll'].includes(s.overflowX) && e.scrollWidth > e.clientWidth + 2; }).length;
 const smallTap = [...document.querySelectorAll('button, a.btn, input[type=submit]')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.height < 44; }).length;
 const nav = performance.getEntriesByType('navigation')[0];
 return {docW: document.documentElement.scrollWidth, W, H: document.documentElement.scrollHeight, nodes: document.getElementsByTagName('*').length,
         overflow: [...new Set(out)].slice(0, 8), inner_scrollers: scrollers, small_tap: smallTap,
         load_ms: nav ? Math.round(nav.loadEventEnd - nav.startTime) : null, kb: nav ? Math.round(nav.transferSize/1024) : null};
}"""


def cookies_for(phone, idx, cart=None):
    p = Person(idx, phone)
    b = login("shots", p)
    if cart:
        cart(b)
    return [{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in b.s.cookies]


def phone_idx(phone):
    return int(phone[-4:]) if phone.startswith("+7926") else int(phone[-3:]) + 200


SCREENS = []
def S(name, path, who=None, cart=None):
    SCREENS.append((name, path, who, cart))

JUDGE1 = ("+79261001001", 1001)
S("Календарь (гость)", "/")
S("Турнир S1: программа (гость)", "/t/3/")
S("Турнир S1: стартовые (гость)", "/t/3/?tab=starts")
S("Турнир S1: результаты (гость)", "/t/3/?tab=results")
S("Турнир S3 выездка: результаты (гость)", "/t/5/?tab=results")
S("Мастер, шаг 2 — 20 маршрутов (судья)", "/organizer/t/3/step/2/", JUDGE1)
S("Рассмотрение заявок S1 (судья)", "/organizer/t/3/entries/", JUDGE1)
S("Проведение S1 (судья)", "/organizer/t/3/conduct/", JUDGE1)
S(f"Порядок стартов, самый большой маршрут (судья)", f"/organizer/t/3/conduct/p/{META['big']}/", JUDGE1)
S("Экран судьи у поля (судья)", f"/organizer/t/3/conduct/p/{META['big']}/field/", JUDGE1)
S("Кабинет судьи", "/cabinet/", JUDGE1)
S("Лист судьи E, выездка S3", f"/organizer/t/5/conduct/p/{META['d3']}/judge/E/", ("+79263001002", 3002))
S("Судья-подсчёт, 5 судей S3", f"/organizer/t/5/conduct/p/{META['d3']}/scorer/", ("+79263001010", 3010))
S("Заявка с наибольшим числом стартов (тренер)", f"/applications/{META['app']}/", (META["app_user"], None))
S("Мои заявки (тренер)", "/my/applications/", (META["app_user"], None))


def big_cart(b):
    """Тренер собирает заявку на новый турнир S3 из всех своих пар — как на странице подачи."""
    b.get("/t/5/apply/")
    if b.page.form(button="Добавить выбранные"):
        return
    f = b.page.form(has_field="athlete")
    if not f:
        return
    ath = [o.get("value") for o in f.find("select", attrs={"name": "athlete"}).find_all("option") if o.get("value")]
    hor = [o.get("value") for o in f.find("select", attrs={"name": "horse"}).find_all("option") if o.get("value")]
    for a, h in list(zip(ath, hor))[:14]:
        f = b.page.form(has_field="athlete")
        b.submit(f, {"athlete": a, "horse": h}, button_name="action", button_value="pair_add")


S("Подача заявки: 14 пар × 10 езд (тренер)", "/t/5/apply/", (META["app_user"], None), big_cart)
S("Мои судейские листы (спортсмен S4)", f"/entries/{META['e4']}/sheets/", (META["e4_user"], None))
S("Денники и услуги: брони (конюшня S1)", "/organizer/t/3/stables/?tab=bookings&state=confirmed", ("+79261001003", 1003))
S("Денники и услуги: заказы услуг (конюшня S1)", "/organizer/t/3/stables/?tab=orders&show=all", ("+79261001003", 1003))
S("Результаты S5 с длинными ФИО и кличками (гость)", "/t/7/?tab=results&day=2026-11-28")
S("Лист судьи E, возвращённый на исправление (S5)", f"/organizer/t/7/conduct/p/{META['s5_d']}/judge/E/", ("+79265001003", 5003))
S("Регистрация (гость)", "/register/")
S("Мои судейские листы, 5 судей (представитель S3)", f"/entries/{META['e3']}/sheets/", (META["e3_user"], None))


def pw_for(phone, idx):
    if idx is not None:
        return idx
    return q(f"from apps.accounts.models import User\nout=0") or 0


results = []
with sync_playwright() as p:
    br = p.chromium.launch(executable_path="/opt/google/chrome/chrome", args=["--no-sandbox"])
    cache = {}
    for name, path, who, cart in SCREENS:
        cookies = None
        if who:
            phone, idx = who
            if idx is None:
                idx = int(phone[-4:]) if phone.startswith("+7926") else int(phone[-4:])
                if phone.startswith("+7918200"):
                    idx = int(phone[-4:])
            key = (phone, bool(cart))
            if key not in cache:
                cache[key] = cookies_for(phone, idx, cart)
            cookies = cache[key]
        for w in WIDTHS:
            ctx = br.new_context(viewport={"width": w, "height": 900 if w > 400 else 800}, device_scale_factor=1)
            if cookies:
                ctx.add_cookies(cookies)
            pg = ctx.new_page()
            t = time.perf_counter()
            pg.goto("http://127.0.0.1:8000" + path, wait_until="load", timeout=120000)
            ms = round((time.perf_counter() - t) * 1000)
            m = pg.evaluate(JS)
            m.update(name=name, path=path, width=w, wall_ms=ms, url=pg.url)
            fn = f"{OUT}/{len(results):02d}_{w}.png"
            pg.screenshot(path=fn, full_page=False)
            if w == 360:
                pg.screenshot(path=f"{OUT}/{len(results):02d}_{w}_full.png", full_page=True, timeout=120000) if m["H"] < 30000 else None
            m["shot"] = fn
            results.append(m)
            print(name, w, m["docW"], m["H"], m["nodes"], ms, m["overflow"][:3], flush=True)
            ctx.close()
    br.close()
json.dump(results, open(f"{OUT}/metrics.json", "w"), ensure_ascii=False, indent=1)
