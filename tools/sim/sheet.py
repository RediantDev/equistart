"""Листы-обзоры: по одному экрану каждого шаблона URL (первая роль, у которой он есть), верх экрана на ширине W; склейка по N.
python3 sheet.py 360 [full]"""
import json, re, sys, os
sys.path.insert(0, "/home/claude/sim")
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
from flows import login
from people import Person

W = int(sys.argv[1])
FULL = "full" in sys.argv
ROLES = {
    "организатор S1": ("+79261001001", 1001), "конюшня S1": ("+79261001003", 1003), "судья на букве S3": ("+79263001002", 3002),
    "судья-подсчёт S3": ("+79263001010", 3010), "тренер, большая заявка": ("+79260003106", 3106), "спортсмен S3": ("+79260004100", 4100),
    "клуб": ("+79266001001", 6001), "модератор": ("+79160000000", 0), "гость": None,
}
R = json.load(open("/home/claude/sim/audit/results.json"))
pat = lambda u: re.sub(r"\d+", "{n}", u)
seen, todo = set(), []
for r in R:
    if r["width"] != 1440 or r.get("status", 200) != 200:
        continue
    k = pat(r["url"])
    if k in seen or any(x in k for x in ("/privacy/", "/consent/", "publication-consent", "/notifications/?")):
        continue
    seen.add(k)
    todo.append((r["role"], r["url"]))
OUT = f"/home/claude/sim/sheets/{W}"
os.makedirs(OUT, exist_ok=True)
shots = []
with sync_playwright() as p:
    br = p.chromium.launch(executable_path="/opt/google/chrome/chrome", args=["--no-sandbox"])
    ctxs = {}
    for role, url in todo:
        if role not in ctxs:
            ctx = br.new_context(viewport={"width": W, "height": 800 if W < 500 else 1000})
            who = ROLES.get(role)
            if who:
                b = login("sheet", Person(who[1], who[0]))
                ctx.add_cookies([{"name": c.name, "value": c.value, "domain": "127.0.0.1", "path": "/"} for c in b.s.cookies])
            ctxs[role] = ctx
        pg = ctxs[role].new_page()
        pg.goto("http://127.0.0.1:8000" + url, wait_until="load")
        fn = f"{OUT}/{len(shots):03d}.png"
        pg.screenshot(path=fn, full_page=FULL)
        pg.close()
        shots.append((fn, f"{len(shots)} {role}: {url}"))
    br.close()
per = 6 if W < 500 else 4
cols = 3 if W < 500 else 2
for i in range(0, len(shots), per):
    imgs = [(Image.open(f), lab) for f, lab in shots[i:i + per]]
    sc = 0.6 if W < 500 else 0.5
    tw = int(W * sc)
    th = max(int(im.height * sc) for im, _ in imgs) + 30
    th = min(th, 2400)
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 10), rows * th), "white")
    d = ImageDraw.Draw(sheet)
    for j, (im, lab) in enumerate(imgs):
        im = im.resize((tw, int(im.height * sc)))
        x, y = (j % cols) * (tw + 10), (j // cols) * th
        d.text((x + 2, y + 2), lab[:70], fill=(200, 0, 0))
        sheet.paste(im.crop((0, 0, tw, th - 30)), (x, y + 28))
    sheet.save(f"{OUT}/sheet_{i // per:02d}.png")
json.dump(shots, open(f"{OUT}/index.json", "w"), ensure_ascii=False, indent=0)
print("sheets", (len(shots) + per - 1) // per, "screens", len(shots))
