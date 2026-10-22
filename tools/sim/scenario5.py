"""Сценарий 5: сложные случаи — то, что ломается в реальной жизни.
Двухдневный турнир (конкур + выездка), 2 судьи у поля, бригада выездки, конюшня."""
import sys, time, json, re, threading, uuid
sys.path.insert(0, "/home/claude/sim")
from people import Person, R
from flows import *
from browser import Browser, friction
from setup_base import ensure_moderator
from orm import classes_of, q
from tests_data import DRESSAGE_TESTS
import stables_flows as SF

SC = "S5 сложные случаи"
T0 = time.time()
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(dict(name=name, ok=bool(ok), detail=str(detail)[:300]))
    print(("OK  " if ok else "FAIL") + " " + name + (f" — {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        friction(SC, "проверка", name, str(detail)[:300], "critical")


mod = ensure_moderator()
A = Person(5001, "+79265001001", female=False, level="confident")   # главный судья
B = Person(5002, "+79265001002", female=True, level="medium")       # второй судья у поля
E = Person(5003, "+79265001003", female=True, level="novice")       # судья выездки на E
H = Person(5004, "+79265001004", female=False, level="medium")      # судья на H
S = Person(5005, "+79265001005", female=True, level="medium")       # судья-подсчёт
ab_, bb_, eb, hb, sb = [reg_or_login(SC, p, ["judge"]) for p in (A, B, E, H, S)]
D1, D2 = "2026-11-28", "2026-11-29"
TESTS = {}
import os
spec = dict(title="Кубок «Сложный случай» — конкур и выездка" + os.environ.get("SFX", ""), start=D1, end=D2, venue=("КСК «Отрадное»", "mos", "Москва", "Пятницкое ш., 1"),
            contact_name=A.fio, contact=A.phone, deadline="2026-11-25", level="club",
            before_programs=lambda b, pk: TESTS.update(pp=create_test(b, pk, **DRESSAGE_TESTS["pp_youth"])),
            programs=[
                dict(discipline="jumping", day=D1, time="09:00", type="clear_speed", heights="60, 70", templates=[1, 7]),
                dict(discipline="jumping", day=D1, time="12:00", type="jumpoff", heights="100", templates=[2, 5, 7]),
                dict(discipline="dressage", day=D2, time="10:00", test=lambda: TESTS["pp"], templates=[2, 7]),
            ])
pk, progs = create_tournament(ab_, spec)
print("tournament", pk, progs, flush=True)

# 1. Модератор возвращает с замечаниями → судья исправляет → повторная проверка
mod.get(f"/moderation/t/{pk}/")
f = mod.page.form(action_contains="decide")
mod.submit(f, {"decision": "return", "comment": "Укажите в положении ветеринарные требования; проверьте адрес."}, drop=("decision", "confirm_venue"))
ab_.get("/cabinet/")
ab_.get(f"/organizer/t/{pk}/step/4/")
check("Замечания модератора видны судье", "ветеринарные требования" in ab_.page.text(), ab_.page.text()[:200])
ab_.get(f"/organizer/t/{pk}/step/1/")
f = ab_.page.form(has_field="title")
ab_.submit(f, {"venue_address": "Пятницкое ш., 1, стр. 2"}, button_name="next")
ab_.get(f"/organizer/t/{pk}/step/4/")
f = ab_.page.form(button="Отправить на проверку")
ab_.submit(f) if f else None
check("Повторная отправка после замечаний", f is not None and "на проверке" in ab_.page.text().lower(), ab_.page.messages()[:2])
print(moderate(mod, pk), flush=True)
for p in (B, E, H, S):
    invite(ab_, pk, p.phone, "judge")
for b in (bb_, eb, hb, sb):
    accept_invites(b)
classes = classes_of(pk)
by_prog = {}
for c in classes:
    by_prog.setdefault(c["program"], []).append(c)

# 2. Участники, в том числе длинные ФИО и клички
idx = 5100
coaches = []
for gi in range(5):
    c = Person(idx, f"+7926{idx:07d}", level=["confident", "medium", "novice", "medium", "confident"][gi]); idx += 1
    cb = reg_or_login(SC, c, ["coach"], " (тренер)")
    pairs, lines = [], []
    for k in range(6):
        by = R.choice([R.randint(2009, 2012), R.randint(1985, 2004)])
        a = Person(idx, f"+7926{idx:07d}", birth_year=by); idx += 1
        if gi == 0 and k == 0:
            a.last, a.first = "Константинопольская-Голенищева", "Александра-Мария"
        aid = add_athlete(cb, a, rank_j=R.choice(["2", "1", "3"]), rank_d=R.choice(["2", "1"]), coach=c.fio)
        hid, hname = add_horse(cb, age=R.randint(8, 14))
        if gi == 0 and k == 0:
            cb.get(f"/horses/{hid}/edit/")
            f = cb.page.form(has_field="name")
            cb.submit(f, {"name": "Сантьяго-де-Компостела Чемпион II"}, drop=("is_pony",))
        ls = []
        for pid in progs:
            ok = [x for x in by_prog[pid] if (not x["by_from"] or by >= x["by_from"]) and (not x["by_to"] or by <= x["by_to"])]
            if ok and R.random() < 0.7:
                ls.append((R.choice(ok)["id"], None))
        if aid and hid and ls:
            pairs.append((aid, hid)); lines.append(ls)
    coaches.append((c, cb, pairs, lines))

# 3. Двойная отправка заявки: две вкладки нажимают «Отправить» одновременно
c0, cb0, pairs0, lines0 = coaches[0]
find_tournament(cb0, spec["title"]); cb0.click("Подать заявку")
for i, (a, h) in enumerate(pairs0):
    f = cb0.page.form(has_field="athlete")
    cb0.submit(f, {"athlete": str(a), "horse": str(h)}, button_name="action", button_value="pair_add")
    f = cb0.page.form(button="Добавить выбранные")
    data = {}
    for cls, _ in lines0[i]:
        for sel in f.find_all("select", attrs={"name": re.compile("^sel_")}):
            o = next((o for o in sel.find_all("option") if (o.get("value") or "").split("|")[0] == str(cls) and not o.has_attr("disabled")), None)
            if o:
                data[sel.get("name")] = o.get("value")
    cb0.submit(f, data, button_name="action", button_value="add_many")
f = cb0.page.form(button="Отправить заявку")
key = f.find(attrs={"name": "key"}).get("value")
res = []
barrier = threading.Barrier(2)


def dbl():
    barrier.wait()
    r = cb0.s.post(f"http://127.0.0.1:8000/t/{pk}/apply/", data={"action": "submit", "key": key, "contact_name": c0.fio, "contact_phone": c0.phone,
                                                                "csrfmiddlewaretoken": cb0.s.cookies.get("csrftoken")},
                   headers={"Referer": f"http://127.0.0.1:8000/t/{pk}/apply/"})
    res.append(r.url)
th = [threading.Thread(target=dbl) for _ in range(2)]
[t.start() for t in th]; [t.join() for t in th]
n_apps = q(f"out=Application.objects.filter(tournament_id={pk}, submitted_by__phone='{c0.phone}').count()")
check("Двойное нажатие «Отправить заявку» создаёт одну заявку", n_apps == 1, f"заявок: {n_apps}; {res}")
for c, cb, pairs, lines in coaches[1:]:
    apply_pairs(cb, spec["title"], pairs, lambda i, L=lines: L[i])

# 4. Предложение изменения и отмена представителем
c1, cb1, pairs1, _ = coaches[1]
app1 = q(f"out=Application.objects.filter(tournament_id={pk}, submitted_by__phone='{c1.phone}').first().id")
cb1.get(f"/applications/{app1}/")
prop_link = cb1.page.find_link("Изменить", "Предложить")
entry_ids = q(f"out=list(Entry.objects.filter(application_id={app1}).values_list('id', flat=True))")
cb1.get(f"/entries/{entry_ids[0]}/propose/")
f = cb1.page.form(has_field="horse")
new_h = [o.get("value") for o in f.find("select", attrs={"name": "horse"}).find_all("option") if o.get("value")]
cur_h = q(f"out=Entry.objects.get(pk={entry_ids[0]}).horse_id")
alt = next((x for x in new_h if x != str(cur_h)), None)
cb1.submit(f, {"horse": alt})
check("Предложение изменения отправлено", any("Предложение отправлено" in m for m in cb1.page.messages()), cb1.page.messages()[:2])
cb1.get(f"/applications/{app1}/")
fc = next((x for x in cb1.page.forms() if "cancel" in (x.get("action") or "")), None)
if fc:
    cb1.submit(fc, {"entry": [str(entry_ids[-1])]})
    f2 = cb1.page.form(action_contains="cancel")
    cb1.submit(f2, {"confirm": "1"}) if f2 else None
canc = q(f"out=Entry.objects.get(pk={entry_ids[-1]}).decision")
check("Представитель отменил выступление до старта", canc == "cancelled", canc)

# 5. Два судьи одновременно принимают «все по фильтру»
pend_before = q(f"out=Entry.objects.filter(tournament_id={pk}, decision='pending').count()")
for b in (ab_, bb_):
    b.get(f"/organizer/t/{pk}/entries/?decision=pending")
forms = []
for b in (ab_, bb_):
    f = b.page.form(action_contains="decide")
    b.submit(f, {"action": "accept", "scope": "filter"})
    forms.append((b, b.page.form(action_contains="decide")))
out = []
barrier = threading.Barrier(2)


def dec(b, f):
    from browser import form_defaults
    data = dict(form_defaults(f)); data["csrfmiddlewaretoken"] = b.s.cookies.get("csrftoken")
    barrier.wait()
    r = b.s.post(f"http://127.0.0.1:8000/organizer/t/{pk}/entries/decide/", data=data, headers={"Referer": f"http://127.0.0.1:8000/organizer/t/{pk}/entries/"})
    out.append((r.status_code, re.findall(r"Выполнено для \d+ из \d+|Не выполнено для \d+: [^<(]+", r.text)))
th = [threading.Thread(target=dec, args=x) for x in forms]
[t.start() for t in th]; [t.join() for t in th]
acc = q(f"out=Entry.objects.filter(tournament_id={pk}, decision='accepted').count()")
pend = q(f"out=Entry.objects.filter(tournament_id={pk}, decision='pending').count()")
audits = q(f"from apps.core.models import AuditLog\nout=AuditLog.objects.filter(action='entry.accept', target_type='entry', target_id__in=list(Entry.objects.filter(tournament_id={pk}).values_list('id', flat=True))).count()")
check("Одновременное решение двух судей: каждое выступление принято один раз, без ошибок 500", all(s < 500 for s, _ in out) and audits == acc and pend == 0,
      f"было на рассмотрении {pend_before}, принято {acc}, записей истории {audits}, осталось {pend}; ответы {out}")
# решение по предложению изменения: принять → выступление снова на рассмотрении → принять
prop = q(f"out=ChangeProposal.objects.filter(entry_id={entry_ids[0]}).first().id")
ab_.get(f"/applications/{app1}/")
fp = next((x for x in ab_.page.forms() if f"/proposals/{prop}/decide/" in (x.get("action") or "")), None)
if fp:
    ab_.submit(fp, {"decision": "accept"}, drop=("decision",))
st = q(f"out=[Entry.objects.get(pk={entry_ids[0]}).decision, Entry.objects.get(pk={entry_ids[0]}).horse_id]")
# решение судьи по предложению само является проверкой: выступление остаётся принятым с новой лошадью
check("Принятое изменение лошади: лошадь в выступлении сменилась", str(st[1]) == str(alt), st)
decide_all(ab_, pk, reject_share=0)

# 6. Правка опубликованного турнира: новая езда → повторная модерация, публика видит прежнюю версию
ab_.get(f"/organizer/t/{pk}/step/2/")
f = ab_.page.form(action_contains="programs/add")
ab_.submit(f, {"discipline": "jumping", "day": D2, "program_type": "no_time", "heights_text": "50", "start_time": "15:00"}, drop=("discipline",))
m = re.search(r"#program-(\d+)", ab_.page.url)
if m:
    f = ab_.page.form(action_contains=f"programs/{m.group(1)}/classes/from-template")
    ab_.submit(f, button_name="template", button_value="12")
pub_progs = len(q(f"out=Tournament.objects.get(pk={pk}).snapshot['programs']"))
check("После правки публика видит прежнюю программу до одобрения", pub_progs == 3, f"программ в опубликованной версии: {pub_progs}")
ab_.get(f"/organizer/t/{pk}/step/4/")
f = ab_.page.form(button="Отправить на проверку")
if f:
    ab_.submit(f)
moderate(mod, pk)
pub_progs = len(q(f"out=Tournament.objects.get(pk={pk}).snapshot['programs']"))
check("После повторной модерации новая программа опубликована", pub_progs == 4, pub_progs)

# 7. Поздняя заявка на месте: судья вносит участника без аккаунта
pl = Person(idx, "", birth_year=1999); idx += 1
pl.last, pl.first = "Опоздавший", "Пётр"
aid = add_athlete(ab_, pl, rank_j="3")
hid, _ = add_horse(ab_, age=10)
ab_.get(f"/organizer/t/{pk}/entries/")
if not ab_.click("Добавить участника"):
    friction(SC, A.fio, "Заявки турнира", "Нет кнопки «Добавить участника»", "high")
late_cls = [c for c in by_prog[progs[1]] if (not c["by_from"] or c["by_from"] <= 1999) and (not c["by_to"] or c["by_to"] >= 1999)][0]
f = ab_.page.form(has_field="athlete")
ab_.submit(f, {"athlete": str(aid), "horse": str(hid)}, button_name="action", button_value="pair_add")
f = ab_.page.form(button="Добавить выбранные")
sel = next((s for s in f.find_all("select") if s.find("option", attrs={"value": str(late_cls["id"])})), None)
ab_.submit(f, {sel.get("name"): str(late_cls["id"])}, button_name="action", button_value="add_many")
f = ab_.page.form(button="Отправить заявку")
ab_.submit(f, button_name="action", button_value="submit")
late = q(f"out=Entry.objects.filter(tournament_id={pk}, athlete_id={aid}).values_list('decision', flat=True).first()")
check("Поздняя заявка судьи сразу принята", late == "accepted", late)

# 8. Объединение зачётов в маршруте 1 (мало пар в «Дети»)
p0 = progs[0]
kids = next(c for c in by_prog[p0] if c["name"] == "Дети")
openc = next(c for c in by_prog[p0] if c["name"].startswith("Общий"))
ab_.get(f"/organizer/t/{pk}/conduct/p/{p0}/")
fm = next((x for x in ab_.page.forms() if "/merge/" in (x.get("action") or "") and x.find(attrs={"name": "tclass", "value": str(kids["id"])})), None)
if fm:
    ab_.submit(fm, {"into": str(openc["id"])})
merged = q(f"out=TournamentClass.objects.get(pk={kids['id']}).merged_into_id")
check("Объединение зачётов «Дети» + «Общий» до протокола", merged == openc["id"], merged)

# 9. Жеребьёвка, протокол, снятие пары после публикации
for pid in progs[:2]:
    ab_.get(f"/organizer/t/{pk}/conduct/p/{pid}/")
    ab_.submit(ab_.page.form(action_contains="/course/"), {"main_length": "380", "main_speed": "350"})
    ab_.submit(ab_.page.form(action_contains="/draw/"))
    ab_.submit(ab_.page.form(action_contains="/publish/"))
fw = next((x for x in ab_.page.forms() if "/status/" in (x.get("action") or "")), None)
if fw:
    ab_.submit(fw, button_name="status", button_value="withdrawn")
    ab_.submit(ab_.page.form(action_contains="/publish/"))
g = Browser(SC, "гость")
g.get(f"/t/{pk}/?tab=starts&day={D1}")
check("Снятая после публикации пара отмечена «Снят» в протоколе", "Снят" in g.page.text())
check("В стартовом протоколе конкура нет времени стартов", "Время" not in " ".join(th.get_text() for th in g.page.soup.select("th")))

# 10. Судья у поля: повтор отправки при обрыве связи не создаёт дубль
p1 = progs[1]
bb_.get(f"/organizer/t/{pk}/conduct/p/{p1}/field/")
f = bb_.page.form(action_contains="field/save")
entry = f.find(attrs={"name": "entry"}).get("value")
ck = f.find(attrs={"name": "client_key"}).get("value")
data = {"entry": entry, "stage": "main", "knockdowns": "1", "refusals": "0", "time": "61,23", "status": "ok", "client_key": ck}
for _ in range(3):
    bb_.post(f"/organizer/t/{pk}/conduct/p/{p1}/field/save/", data, headers={"X-Requested-With": "fetch"})
rounds = q(f"out=[JumpRound.objects.filter(entry_id={entry}).count(), JumpRound.objects.get(entry_id={entry}, round='main').revision]")
check("Тройная отправка с одним ключом — один гит, без новых версий", rounds == [1, 1], rounds)
s, pubs = conduct_jumping(bb_, pk, p1)
s2, pubs2 = conduct_jumping(ab_, pk, p0)
# 11. Исправление опубликованного результата → версия 2
e_fix = q(f"out=Entry.objects.filter(program_id={p1}, result__place=1).first().id")
bb_.get(f"/organizer/t/{pk}/conduct/p/{p1}/field/?stage=main&entry={e_fix}")
f = bb_.page.form(action_contains="field/save")
bb_.submit(f, {"knockdowns": "2", "time": "65,40"})
cls_fix = q(f"out=Entry.objects.get(pk={e_fix}).tclass.merged_into_id or Entry.objects.get(pk={e_fix}).tclass_id")
bb_.post(f"/organizer/t/{pk}/conduct/p/{p1}/publish_results/", {"tclass": cls_fix})
ver = q(f"out=ResultPublication.objects.filter(tclass_id={cls_fix}).order_by('-version').first().version")
g.get(f"/t/{pk}/?tab=results&day={D1}")
check("Исправление результата после публикации — версия 2 с пометкой «исправлено»", ver >= 2 and "исправлено" in g.page.text(), ver)
hist = q(f"from apps.core.models import AuditLog\nout=AuditLog.objects.filter(target_type='entry', target_id={e_fix}, action='round.save').count()")
check("История сохранила прежний и новый результат", hist >= 2, hist)

# 12. Выездка: бригада 3 судьи + подсчёт, возврат листа и исправление оценки
p2 = progs[2]
set_panel(ab_, pk, p2, [("C", A.phone, "", "1К", "Москва", "all"), ("E", E.phone, "", "2К", "Москва", "all"),
                        ("H", H.phone, "", "1К", "Тверская обл.", "all"), ("SCORER", S.phone, "", "ВК", "Москва", "all")])
ab_.get(f"/organizer/t/{pk}/conduct/p/{p2}/")
ab_.submit(ab_.page.form(action_contains="/draw/")); ab_.submit(ab_.page.form(action_contains="/publish/"))
for L, b in (("C", ab_), ("E", eb), ("H", hb)):
    judge_all(b, pk, p2, L, skill=6.8)
# E пытается увидеть лист H до подписи — 403
r = eb.get(f"/organizer/t/{pk}/conduct/p/{p2}/judge/H/")
check("Судья E не видит лист судьи H", r.status == 403, r.status)
ent = q(f"out=Entry.objects.filter(program_id={p2}, decision='accepted').first().id")
sb.post(f"/organizer/t/{pk}/conduct/p/{p2}/scorer/return/", {"entry": ent, "letter": "E", "reason": "Пропущена оценка за упражнение 7 — проверьте"})
eb.get("/notifications/")
check("Судья E получил уведомление о возврате листа", "Лист возвращён" in eb.page.text())
eb.get(f"/organizer/t/{pk}/conduct/p/{p2}/judge/E/?entry={ent}")
check("Возвращённый лист показывает причину", "Пропущена оценка" in eb.page.text())
f = eb.page.form(action_contains="/sign/")
marks = {el.get("name"): el.get("value") for el in f.find_all("input", attrs={"name": re.compile("^m_")})}
k0 = sorted(marks)[0]
marks[k0] = "9"
eb.submit(f, marks)
hist = q(f"from apps.core.models import AuditLog\nout=AuditLog.objects.filter(target_type='entry', target_id={ent}, action__startswith='dressage').count()")
check("История листа выездки: оценки, подпись, возврат, повторная подпись", hist >= 4, hist)
print("scorer", scorer_publish(sb, pk, p2), flush=True)

# 13. Удалить зачёт, в который уже поданы заявки, — понятный отказ
ab_.get(f"/organizer/t/{pk}/step/2/")
fd = next((x for x in ab_.page.forms() if "/classes/" in (x.get("action") or "") and "/delete/" in (x.get("action") or "")), None)
if fd:
    ab_.submit(fd)
msgs = ab_.page.messages()
check("Удаление зачёта с заявками — понятное сообщение", any("уже поданы заявки" in m.lower() or "нельзя" in m.lower() or "проверке" in m.lower() for m in msgs), msgs[:2])

json.dump(dict(pk=pk, progs=progs, checks=CHECKS), open("/home/claude/sim/s5_result.json", "w"), ensure_ascii=False, indent=1)
print("CHECKS", sum(c["ok"] for c in CHECKS), "/", len(CHECKS), "TOTAL", round(time.time() - T0), flush=True)
