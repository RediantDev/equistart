"""Сценарий 6: клубы вместе с турнирами. Регистрация клубов с ошибками новичков, модерация, дубль названия, фото и новости,
клуб как место проведения, клуб в команде турнира («Конюшня и услуги»), свой турнир клуба, правка названия, скрытие новости."""
import sys, time, io, json, os
sys.path.insert(0, "/home/claude/sim")
from PIL import Image, ImageDraw
from people import Person, R
from flows import *
from browser import Browser, friction
from setup_base import ensure_moderator

SC = "S6 клубы"
SFX = os.environ.get("SFX", "")
T0 = time.time()
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(("OK  " if ok else "FAIL"), name, detail, flush=True)


def img(w=1200, h=800, color=(70, 120, 90), text="", fmt="JPEG"):
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im)
    for i in range(0, w, 60):
        d.line([(i, 0), (i + h // 2, h)], fill=tuple(min(255, c + 30) for c in color), width=8)
    if text:
        d.rectangle([40, h - 140, w - 40, h - 40], fill=(250, 250, 245))
        d.text((70, h - 110), text, fill=(30, 30, 30))
    buf = io.BytesIO(); im.save(buf, fmt); return buf.getvalue()


mod = ensure_moderator()

CLUBS = [  # (название, регион, город, адрес, уровень)
    (f"КСК «Солнечная долина»{SFX}", "kda", "Краснодар", "пос. Индустриальный, ул. Конная, 12", "confident"),
    (f"Конный клуб «Аллюр»{SFX}", "rya", "Рязань", "Окружная дорога, 185 км", "medium"),
    (f"КСШ «Олимп»{SFX}", "tat", "Казань", "ул. Спортивная, 3", "novice"),
    (f"КСК «Северное сияние» — конноспортивный клуб, школа верховой езды и пансион для лошадей{SFX}", "spe", "Санкт-Петербург",
     "Приморское шоссе, 18-й км, участок 4, строение 2 (въезд со стороны залива, шлагбаум)", "medium"),
    (f"Клуб «Пегас»{SFX}", "mos", "Москва", "ул. Конная, 1", "novice"),
    (f"КСК «Пегас»{SFX}", "mos", "Москва", "Бутово, ул. Поляны, 40", "confident"),   # похожее название в том же городе
]
REG = {"kda": "Краснодарский", "rya": "Рязанская", "tat": "Татарстан", "spe": "Санкт-Петербург", "mos": "Москва"}

# ---------- 1. регистрация клубов и профили ----------
club_b, club_people = [], []
for i, (name, reg, city, addr, lvl) in enumerate(CLUBS):
    p = Person(6001 + i, f"+7926600{1001 + i:04d}", level=lvl)
    b = reg_or_login(SC, p, ["club"], " (клуб)")
    club_b.append(b); club_people.append(p)
    b.get("/cabinet/")
    if not b.click("Создать профиль клуба", "Мой клуб", name[:10]):
        b.friction("Кабинет клуба", "Нет входа в профиль клуба из кабинета", "high")
        b.get("/my/club/")
    f = b.page.form(has_field="settlement")
    if not f:
        b.friction("Мой клуб", "Нет формы профиля клуба", "critical"); continue
    # регион ищем по названию, как человек в выпадающем списке
    sel = f.find("select", attrs={"name": "region"})
    rv = next((o.get("value") for o in sel.find_all("option") if REG[reg] in o.get_text()), reg)
    full = {"name": name, "region": rv, "settlement": city, "address": addr, "phone": p.phone,
            "description": "Крытый манеж 20×60, открытый плац с песчаным грунтом, 60 денников, гостиница для всадников.\nПроводим клубные и региональные старты по конкуру и выездке.",
            "email": "", "site": "", "socials": "https://vk.com/club_test https://t.me/club_test"}
    if lvl == "novice":
        # новичок: жмёт «отправить» без адреса и контактов, потом грузит логотип в GIF
        b.submit(f, dict(full, address="", phone=""), button_name="submit", button_value="1")
        msgs = " ".join(b.page.messages())
        errs = " ".join(b.page.errors())
        check(f"{name[:18]}: сразу видно оба пропуска — адрес и контакт", "Обязательное" in errs and "контакт" in errs, errs[:160])
        f = b.page.form(has_field="settlement")
        gif = io.BytesIO(); Image.new("RGB", (200, 200), (200, 40, 40)).save(gif, "GIF")
        b.submit(f, full, files={"logo": ("logo.gif", gif.getvalue(), "image/gif")}, button_name="submit", button_value="1")
        errs = b.page.errors()
        check(f"{name[:18]}: GIF-логотип отклонён понятным текстом", any("JPEG" in e for e in errs), str(errs[:1]))
        if not errs:
            b.friction("Мой клуб — логотип", "GIF принят или ошибка не видна рядом с полем", "high")
        f = b.page.form(has_field="settlement")
    elif lvl == "medium" and i == 1:
        # средний: сохраняет черновик, уходит, возвращается и отправляет
        b.submit(f, full)
        b.get("/cabinet/")
        b.click("Мой клуб", name[:10], "Черновик")
        f = b.page.form(has_field="settlement")
    b.submit(f, full, files={"logo": ("logo.png", img(400, 400, (40, 90, 160), fmt="PNG"), "image/png")}, button_name="submit", button_value="1")
    ms = b.page.messages()
    if not any("модератору" in m for m in ms):
        b.friction("Мой клуб", f"Не отправился на проверку: {ms[:2]} {b.page.errors()[:2]}", "high")
    if "Пегас" in name and i == 5:
        check("Клуб видит предупреждение о похожем клубе", "похожий клуб" in b.page.text(), "")

# ---------- 2. модерация ----------
mod.get("/cabinet/")
if not mod.click("Клубы на проверке"):
    mod.friction("Кабинет модератора", "Нет входа в очередь клубов", "high")
    mod.get("/moderation/clubs/")
q_links = [(t, h) for t, h in mod.page.links() if "/moderation/clubs/" in h and h.rstrip("/").split("/")[-1].isdigit()]
q_ids = sorted({int(h.rstrip("/").split("/")[-1]) for _, h in q_links})
check("В очереди модератора все 6 клубов", len(q_ids) >= 6, str(len(q_ids)))
from orm import q as orm_q
ids = orm_q(f"""
from apps.clubs.models import Club
out = {{c.name: c.pk for c in Club.objects.filter(name__in={[c[0] for c in CLUBS]!r})}}
""")
CID = {n: ids[n] for n in ids}
for name, *_ in CLUBS:
    cid = CID[name]
    mod.get(f"/moderation/clubs/{cid}/")
    if name.startswith("Клуб «Пегас»") or name.startswith("КСК «Пегас»"):
        check(f"Модератор видит дубль для {name[:14]}", "Похожие клубы" in mod.page.text())
    if name.startswith("Клуб «Пегас»"):
        f = mod.page.form(button="Вернуть")
        mod.submit(f, {"comment": "Похоже на дубль КСК «Пегас» в Бутово. Если это другой клуб — уточните адрес и название."}, button_name="decision", button_value="return")
        continue
    if "Олимп" in name:
        f = mod.page.form(button="Вернуть")
        mod.submit(f, {"comment": ""}, button_name="decision", button_value="return")
        check("Пустое замечание не принимается", any("Напишите" in m for m in mod.page.messages()), str(mod.page.messages()[:1]))
        mod.get(f"/moderation/clubs/{cid}/")
        f = mod.page.form(button="Вернуть")
        mod.submit(f, {"comment": "Добавьте номер дома и сайт или соцсеть клуба"}, button_name="decision", button_value="return")
        continue
    f = mod.page.form(button="Подтвердить")
    mod.submit(f, button_name="decision", button_value="confirm")

# клуб «Олимп» исправляет по замечанию
ob = club_b[2]
ob.get("/cabinet/")
check("Замечание модератора видно в кабинете клуба", "Добавьте номер дома" in ob.page.text())
ob.click("Мой клуб", "Олимп", "Нужны исправления")
f = ob.page.form(has_field="settlement")
ob.submit(f, {"address": "ул. Спортивная, д. 3, корп. 1", "site": "https://olimp-kazan.example.ru"}, button_name="submit", button_value="1")
mod.get(f"/moderation/clubs/{CID[CLUBS[2][0]]}/")
mod.submit(mod.page.form(button="Подтвердить"), button_name="decision", button_value="confirm")
# дубль «Пегас»: представитель понимает, что это другой клуб, и уточняет
pb = club_b[4]
pb.get("/my/club/")
f = pb.page.form(has_field="settlement")
pb.submit(f, {"name": f"Детский конный клуб «Пегасик»{SFX}", "address": "ул. Конная, 1, Измайловский парк"}, button_name="submit", button_value="1")
CID["Пегасик"] = CID.pop(CLUBS[4][0])
mod.get(f"/moderation/clubs/{CID['Пегасик']}/")
mod.submit(mod.page.form(button="Подтвердить"), button_name="decision", button_value="confirm")

# ---------- 3. фото ----------
for i, b in enumerate(club_b[:4]):
    for k in range(3 if i else 8):
        b.get("/my/club/")
        f = b.page.form(action_contains="/photos/add/")
        b.submit(f, {"caption": ["Крытый манеж", "Плац", "Конюшня", "Леваду", "Гостиница", "Трибуны", "Разминка", "Кафе"][k]},
                 files={"file": (f"p{k}.jpg", img(1600, 1000, (60 + 20 * k, 110, 70 + 10 * i), f"Фото {k+1}"), "image/jpeg")})
# 12-МБ фото и pdf под видом jpg
b = club_b[1]
b.get("/my/club/")
b.submit(b.page.form(action_contains="/photos/add/"), files={"file": ("big.jpg", b"\xff\xd8\xff" + b"0" * (11 * 1024 * 1024), "image/jpeg")})
check("Фото больше 10 МБ отклонено с понятным текстом", any("10 МБ" in m for m in b.page.messages()), str(b.page.messages()[:1]))
b.submit(b.page.form(action_contains="/photos/add/"), files={"file": ("doc.jpg", pdf_bytes(), "image/jpeg")})
check("PDF под видом JPEG отклонён", any("JPEG или PNG" in m for m in b.page.messages()), str(b.page.messages()[:1]))

# ---------- 4. публичный список ----------
g = Browser(SC, "Гость", "novice")
g.get("/")
if not g.click("Клубы"):
    g.friction("Шапка", "Нет ссылки на каталог клубов", "high"); g.get("/clubs/")
check("Каталог показывает подтверждённые клубы", all(n.split("«")[1][:6] in g.page.text() for n in [CLUBS[0][0], CLUBS[1][0], CLUBS[5][0]]))
g.submit(g.page.form(has_field="q"), {"q": "казань"})
check("Поиск по городу находит клуб", "Олимп" in g.page.text() and "Аллюр" not in g.page.text())
g.get("/clubs/")
sel = g.page.form(has_field="region").find("select", attrs={"name": "region"})
rv = next(o.get("value") for o in sel.find_all("option") if "Рязанская" in o.get_text())
g.submit(g.page.form(has_field="region"), {"region": rv, "q": ""})
check("Фильтр по региону", "Аллюр" in g.page.text() and "Олимп" not in g.page.text())
r = g.get(f"/clubs/{CID[CLUBS[0][0]]}/")
check("Телефон управляющего не утёк в чужие места", "Konkur" not in r.text())

# ---------- 5. организатор проводит турнир в клубе, клуб в команде ----------
org = Person(6101, "+79266001101", female=True, level="medium")
jb = reg_or_login(SC, org, ["judge"])
D = "2026-11-21"
sun = CLUBS[0][0]
spec = dict(title=f"Кубок «Солнечной долины» по конкуру{SFX}", start=D, end=D, venue=("", "", "", ""), venue_pick=sun,
            contact_name=org.fio, contact=org.phone, deadline="2026-11-18", level="club",
            programs=[dict(discipline="jumping", day=D, time="10:00", type="no_time", heights="60, 70", templates=[1]),
                      dict(discipline="jumping", day=D, time="12:00", type="clear_speed", heights="90", templates=[7])])
r = create_tournament(jb, spec)
pk = r[0] if isinstance(r, tuple) else r
print("tournament at club", pk, flush=True)
print(moderate(mod, pk), flush=True)
venue_ok = orm_q(f"from apps.tournaments.models import Tournament\nout = Tournament.objects.get(pk={pk}).venue.club.pk if hasattr(Tournament.objects.get(pk={pk}).venue, 'club') else None")
check("Турнир проведён на площадке клуба (venue = клуб)", venue_ok == CID[sun], str(venue_ok))
g.get(f"/clubs/{CID[sun]}/")
check("Турнир виден на странице клуба", spec["title"] in g.page.text())
g.get(f"/t/{pk}/")
check("Со страницы турнира — ссылка на клуб", any(f"/clubs/{CID[sun]}/" in h for _, h in g.page.links()))
g.get(f"/?club={CID[sun]}")
check("Календарь с фильтром по клубу", spec["title"] in g.page.text())

sb = club_b[0]
outsider = club_b[1]
r = outsider.get(f"/organizer/t/{pk}/stables/")
check("Клуб без приглашения не попадает в денники турнира (403)", r.status == 403, str(r.status))
invite(jb, pk, club_people[0].phone, "stable")
accept_invites(sb)
from stables_flows import setup as stables_setup, book as s_book
d0, d1 = stables_setup(sb, pk, D, D)
r = sb.get(f"/organizer/t/{pk}/stables/")
sb.click("Категории и услуги")
check("Клуб в роли «Конюшня и услуги» управляет денниками", r.status == 200 and "Летник" in sb.page.text())
r = sb.get(f"/organizer/t/{pk}/entries/")
check("Конюшня не видит рассмотрение заявок (403)", r.status == 403, str(r.status))

# ---------- 6. клуб создаёт свой турнир ----------
ab = club_b[1]
D2 = "2026-12-12"
spec2 = dict(title=f"Клубные старты «Аллюра» по выездке{SFX}", start=D2, end=D2, venue=("", "", "", ""), venue_pick=CLUBS[1][0],
             contact_name=club_people[1].fio, contact=club_people[1].phone, deadline="2026-12-09", level="club",
             programs=[dict(discipline="dressage", day=D2, time="10:00", test=2, templates=[1])])
r2 = create_tournament(ab, spec2)
pk2 = r2[0] if isinstance(r2, tuple) else r2
print(moderate(mod, pk2), flush=True)
g.get(f"/clubs/{CID[CLUBS[1][0]]}/")
check("Свой турнир клуба на его странице", spec2["title"] in g.page.text())

# ---------- 7. новости ----------
for b, title, tour in [(sb, "Открыт приём заявок на Кубок «Солнечной долины»", spec["title"]), (ab, "Клубные старты по выездке 12 декабря", spec2["title"]),
                       (sb, "Новый грунт в манеже", None)]:
    b.get("/my/club/")
    if not b.click("Написать новость"):
        b.friction("Мой клуб", "Нет кнопки «Написать новость»", "high"); b.get("/my/club/news/new/")
    f = b.page.form(has_field="title")
    sel = f.find("select", attrs={"name": "tournament"})
    tv = next((o.get("value") for o in sel.find_all("option") if tour and tour[:20] in o.get_text()), "") if tour else ""
    if tour and not tv:
        b.friction("Новость клуба", f"Турнир «{tour}» нет в списке привязки", "high")
    b.submit(f, {"title": title, "text": "Подробности — в положении турнира. Денники бронируйте заранее, летник ограничен.\nДо встречи на старте!",
                 "tournament": tv}, files={"photos": ("n.jpg", img(1200, 800, (150, 100, 60), title[:30]), "image/jpeg")})
g.get(f"/clubs/{CID[sun]}/")
check("Новости с фото на странице клуба", "Новый грунт" in g.page.text() and "/clubs/file/news/" in g.page.html)
nid = orm_q(f"from apps.clubs.models import ClubNews\nout = ClubNews.objects.filter(club_id={CID[sun]}, title='Новый грунт в манеже').first().pk")
check("Новость привязана к турниру ссылкой", f"/t/{pk}/" in g.page.html)

# ---------- 8. правка названия ----------
sb.get("/my/club/")
f = sb.page.form(has_field="settlement")
new = f"КСК «Солнечная долина — Кубань»{SFX}"
sb.submit(f, {"name": new})
check("Клуб предупреждён о повторной проверке", any("снова на проверке" in m for m in sb.page.messages()), str(sb.page.messages()[:1]))
g.get(f"/clubs/{CID[sun]}/")
check("Пока проверка — публика видит прежнее название", sun in g.page.text() and new not in g.page.text())
mod.get(f"/moderation/clubs/{CID[sun]}/")
check("Модератор видит, что было опубликовано", "Было опубликовано" in mod.page.text())
mod.submit(mod.page.form(button="Подтвердить"), button_name="decision", button_value="confirm")
g.get(f"/clubs/{CID[sun]}/")
check("После подтверждения — новое название", new in g.page.text())
g.get(f"/t/{pk}/")
check("Опубликованный турнир сохраняет место из снимка", "Солнечная долина" in g.page.text())

# ---------- 9. модератор скрывает новость ----------
mod.get(f"/moderation/clubs/{CID[sun]}/")
f = next(x for x in mod.page.forms() if x.find(attrs={"name": "news", "value": str(nid)}))
mod.submit(f, {"reason": "Реклама стороннего магазина"}, button_name="decision", button_value="hide_news")
g.get(f"/clubs/{CID[sun]}/")
check("Скрытая новость пропала у публики", "Новый грунт" not in g.page.text())
sb.get("/my/club/")
check("Клуб видит причину скрытия", "Реклама стороннего магазина" in sb.page.text())

# ---------- 10. права ----------
r = outsider.get(f"/my/club/news/{nid}/")
check("Чужой клуб не открывает чужую новость (404)", r.status == 404, str(r.status))
r = outsider.post(f"/my/club/news/{nid}/delete/", {})
check("Чужой клуб не удаляет чужую новость (403)", r.status == 403, str(r.status))
coachb = reg_or_login(SC, Person(6201, "+79266002001", level="novice"), ["coach"])
coachb.get("/my/club/")
check("Тренер без роли «Клуб» — подсказка добавить роль", "/cabinet/add-role/" in coachb.page.url)
r = coachb.get(f"/moderation/clubs/")
check("Не модератор — 403 на очереди клубов", r.status == 403, str(r.status))


# ---------- 11. управляющие клуба ----------
helper = Person(6301, "+79266003001", level="novice")
sb.get("/my/club/")
if not sb.click("Управляющие"):
    sb.friction("Мой клуб", "Нет входа в список управляющих", "high"); sb.get("/my/club/team/")
sb.submit(sb.page.form(has_field="phone"), {"phone": "8 (926) 600-30-01"}, button_name="action", button_value="invite")
check("Приглашение управляющего по телефону (ещё без аккаунта)", any("Приглашение отправлено" in m for m in sb.page.messages()), str(sb.page.messages()[:1]))
hb = reg_or_login(SC, helper, ["coach"])   # регистрируется как тренер, роли «Клуб» нет
hb.get("/cabinet/")
if not hb.click("Приглашение управлять клубом", "Приглашения"):
    hb.friction("Кабинет", "Не видно приглашения управлять клубом", "high"); hb.get("/cabinet/invitations/")
f = hb.page.form(action_contains="/my/club/invite/")
check("Приглашение ждало регистрации и видно в кабинете", f is not None)
if f:
    hb.submit(f, button_name="decision", button_value="accept")
check("Управляющий сразу попадает в «Мой клуб»", "/my/club/" in hb.page.url)
f = hb.page.form(has_field="settlement")
hb.submit(f, {"description": "Крытый манеж 20×60, плац, 60 денников. Летом — открытые старты по выездке."})
check("Управляющий правит профиль", any("Сохранено" in m for m in hb.page.messages()))
hb.get("/my/club/team/")
check("Управляющий не может приглашать", hb.page.form(has_field="phone") is None)
# владелец передаёт клуб
sb.get("/my/club/team/")
f = next((x for x in sb.page.forms() if x.find("button", attrs={"value": "transfer"})), None)
sb.submit(f, button_name="action", button_value="transfer")
f = sb.page.form(button="Передать")
check("Передача владения — с подтверждением", f is not None)
sb.submit(f, button_name="action", button_value="transfer")
hb.get("/my/club/team/")
check("Новый владелец может приглашать", hb.page.form(has_field="phone") is not None)

# ---------- 12. модератор объединяет дубль ----------
dupp = Person(6401, "+79266004001", level="medium")
db = reg_or_login(SC, dupp, ["club"], " (клуб)")
db.get("/my/club/")
f = db.page.form(has_field="settlement")
sel = f.find("select", attrs={"name": "region"})
rv = next(o.get("value") for o in sel.find_all("option") if "Краснодарский" in o.get_text())
db.submit(f, {"name": f"Солнечная долина{SFX}", "region": rv, "settlement": "Краснодар", "address": "Индустриальный, Конная 12", "phone": dupp.phone,
              "description": "", "email": "", "site": "", "socials": ""}, button_name="submit", button_value="1")
dup_id = orm_q(f"from apps.clubs.models import Club\nout = Club.objects.get(name='Солнечная долина{SFX}').pk")
mod.get(f"/moderation/clubs/{dup_id}/")
check("Модератор видит похожий клуб", "Похожие клубы" in mod.page.text())
mod.submit(mod.page.form(button="Подтвердить"), button_name="decision", button_value="confirm")  # по ошибке подтвердил
mod.get(f"/moderation/clubs/{dup_id}/")
f = mod.page.form(button="Объединить")
opt = f.find("select", attrs={"name": "main"}).find_all("option")
mv = next((o.get("value") for o in opt if "Кубань" in o.get_text()), "")
check("Основной клуб предложен в списке", bool(mv))
mod.submit(f, {"main": mv}, button_name="decision", button_value="merge")
check("Дубль объединён", any("объединён" in m for m in mod.page.messages()), str(mod.page.messages()[:1]))
g.get(f"/clubs/{dup_id}/")
check("Страница дубля ведёт на основной клуб", f"/clubs/{CID[sun]}/" in g.page.url)
g.get("/clubs/?q=солнечная")
check("В каталоге один клуб «Солнечная долина»", g.page.text().count("Солнечная долина") == 1)

ok = sum(1 for c in CHECKS if c[1])
print(f"CHECKS {ok}/{len(CHECKS)}", flush=True)
json.dump(dict(checks=CHECKS, clubs=CID, t=pk, t2=pk2), open("/home/claude/sim/s6_meta.json", "w"), ensure_ascii=False)
print("TOTAL", round(time.time() - T0), flush=True)
