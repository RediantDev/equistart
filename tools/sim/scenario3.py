"""Сценарий 3: всероссийские соревнования по выездке, 4 дня, много пар. Бригада 5 судей на буквах + судья-подсчёт; дети — 3 судьи."""
import sys, time, json
sys.path.insert(0, "/home/claude/sim")
from people import Person, R
from flows import *
from setup_base import ensure_moderator
from orm import classes_of
from tests_data import DRESSAGE_TESTS, movements, BASIC, COLLECTIVE_STD

SC = "S3 выездка ВС 4 дня"
T0 = time.time()
mod = ensure_moderator()
chief = Person(3001, "+79263001001", female=True, level="confident")      # главный судья, создаёт турнир, сидит на C
judges = [Person(3002 + i, f"+7926300{1002 + i:04d}", female=R.random() < 0.7, level=lv)
          for i, lv in enumerate(["medium", "novice", "confident", "medium", "novice"])]   # E, H, M, B + резерв
sec = Person(3010, "+79263001010", female=True, level="medium")          # главный секретарь — судья-подсчёт
cb = reg_or_login(SC, chief, ["judge"])
jbs = [reg_or_login(SC, p, ["judge"]) for p in judges]
sb = reg_or_login(SC, sec, ["judge"])

days = ["2026-12-03", "2026-12-04", "2026-12-05", "2026-12-06"]
TESTS = {}
FREESTYLE = dict(name="КЮР. Юноши (тест)", short="КЮР Ю", level="Юноши", kind="freestyle", arena="20x60", minutes=5,
                 movements=movements([("", "Остановка и приветствие в начале и в конце", 1), ("", "Собранный шаг", 1), ("", "Прибавленный шаг", 1),
                                      ("", "Собранная рысь", 1), ("", "Прибавленная рысь", 1), ("", "Плечом внутрь направо", 1),
                                      ("", "Плечом внутрь налево", 1), ("", "Принимание направо на рыси", 1), ("", "Принимание налево на рыси", 1),
                                      ("", "Собранный галоп", 1), ("", "Прибавленный галоп", 1), ("", "Смены ног через 4 темпа", 1),
                                      ("", "Смены ног через 3 темпа", 1), ("", "Полупируэт направо", 1), ("", "Полупируэт налево", 1)]),
                 collective="Аллюры | 1", second="Ритм, энергия и эластичность | 4\nГармония всадника и лошади | 4\nХореография | 4\nСтепень сложности | 3\nВыбор и интерпретация музыки | 4",
                 scheme="fei_junior", tie="artistic")


def make_tests(b, pk):
    for key in ("pp_youth", "kp_youth", "mp", "sp1", "bp", "child"):
        TESTS[key] = create_test(b, pk, **DRESSAGE_TESTS[key])
    TESTS["kur"] = create_test(b, pk, **FREESTYLE)
    print("tests", TESTS, flush=True)


T = lambda k: (lambda: TESTS[k])
# (день, езда, зачёты-шаблоны, план пар, бригада 5 или 3)
RIDES = [
    (0, 2, [1], 24, 3), (0, T("pp_youth"), [2], 30, 5), (0, T("mp"), [5, 4], 26, 5),
    (1, T("child"), [1], 22, 3), (1, T("kp_youth"), [2], 28, 5), (1, T("sp1"), [5], 20, 5),
    (2, T("kp_youth"), [3, 4], 18, 5), (2, T("bp"), [5], 14, 5),
    (3, T("kur"), [2], 16, 5), (3, T("mp"), [6, 7], 24, 5),
]
programs = [dict(discipline="dressage", day=days[d], time=f"{9 + (i % 3) * 3:02d}:00", test=t, templates=tp) for i, (d, t, tp, _, _) in enumerate(RIDES)]
spec = dict(title="Всероссийские соревнования по выездке «Зимний Кубок»", start=days[0], end=days[-1],
            venue=("КСК «Виват, Россия!»", "mos", "Москва", "Новорязанское ш., 25"), contact_name=chief.fio, contact=chief.phone,
            deadline="2026-11-28", level="regional", classification=True, before_programs=make_tests, programs=programs)
t1 = time.time()
pk, progs = create_tournament(cb, spec)
print("tournament", pk, progs, "wizard", round(time.time() - t1), "s", flush=True)
for p in judges + [sec]:
    invite(cb, pk, p.phone, "judge")
for b in jbs + [sb]:
    accept_invites(b)
print(moderate(mod, pk), flush=True)
classes = classes_of(pk)
by_prog = {}
for c in classes:
    by_prog.setdefault(c["program"], []).append(c)
json.dump(dict(pk=pk, progs=progs), open("/home/claude/sim/s3_meta.json", "w"))
need = {pid: r[3] for pid, r in zip(progs, RIDES)}


def eligible(c, by, hage):
    if c["by_from"] and by < c["by_from"]: return False
    if c["by_to"] and by > c["by_to"]: return False
    if c["ha_min"] and hage < c["ha_min"]: return False
    if c["ha_max"] and hage > c["ha_max"]: return False
    return True


def pick(by, hage):
    out, used = [], set()
    cands = [(pid, [c for c in by_prog[pid] if eligible(c, by, hage)]) for pid in progs if need[pid] > 0]
    cands = [(pid, cl) for pid, cl in cands if cl]
    # в выездке пара обычно едет связку езд: ПП → КП → КЮР / МП → СП1 → МП
    for pid, cl in cands:
        d = cl[0]["day"]
        if d in used:
            continue
        if R.random() < 0.75:
            out.append((R.choice(cl)["id"], None)); used.add(d); need[pid] -= 1
    return out


idx = 3100
pairs_total = starts = 0
# региональные федерации и тренеры подают группами
for gi in range(14):
    coach = Person(idx, f"+7926{idx:07d}"); idx += 1
    cbx = reg_or_login(SC, coach, ["coach"], " (тренер)")
    pairs, lines = [], []
    for k in range(R.randint(4, 10)):
        kind = R.choices(["child", "youth", "junior", "adult"], [20, 35, 15, 30])[0]
        by = {"child": R.randint(2012, 2014), "youth": R.randint(2009, 2011), "junior": R.randint(2005, 2008), "adult": R.randint(1975, 2004)}[kind]
        hage = R.randint(7, 18)
        lns = pick(by, hage)
        if not lns:
            continue
        a = Person(idx, f"+7926{idx:07d}", birth_year=by); idx += 1
        aid = add_athlete(cbx, a, rank_d=R.choice(["2", "1", "kms", "ms", "1y", "2y"]), coach=coach.fio)
        hid, _ = add_horse(cbx, age=hage)
        if aid and hid:
            pairs.append((aid, hid)); lines.append(lns)
    if pairs:
        r = apply_pairs(cbx, spec["title"], pairs, lambda i: lines[i])
        if r:
            pairs_total += len(pairs); starts += r[1]
    print("coach", gi, coach.level, len(pairs), flush=True)
while sum(max(v, 0) for v in need.values()) > 3:
    kind = R.choices(["child", "youth", "junior", "adult"], [20, 35, 15, 30])[0]
    by = {"child": R.randint(2012, 2014), "youth": R.randint(2009, 2011), "junior": R.randint(2005, 2008), "adult": R.randint(1975, 2004)}[kind]
    hage = R.randint(7, 18)
    lns = pick(by, hage)
    if not lns:
        continue
    a = Person(idx, f"+7926{idx:07d}", birth_year=by); idx += 1
    ab = reg_or_login(SC, a, ["athlete"])
    aid = fill_own_profile(ab, a, rank_d=R.choice(["2", "1", "kms", "ms"]))
    hid, _ = add_horse(ab, age=hage)
    if aid and hid:
        r = apply_pairs(ab, spec["title"], [(aid, hid)], lambda i: [lns][i])
        if r:
            pairs_total += 1; starts += r[1]
print("pairs", pairs_total, "starts", starts, "time", round(time.time() - T0), flush=True)
print("decided", decide_all(cb, pk, reject_share=0.01), flush=True)

LET5 = [("C", chief, "ВК", "Москва"), ("E", judges[0], "1К", "Московская обл."), ("H", judges[1], "1К", "Санкт-Петербург"),
        ("M", judges[2], "МК4*", "Москва"), ("B", judges[3], "1К", "Татарстан")]
b_of = {chief.phone: cb, **{p.phone: b for p, b in zip(judges, jbs)}}
for i, pid in enumerate(progs):
    lets = LET5 if RIDES[i][4] == 5 else [LET5[0], LET5[1], LET5[4]]
    rows = [(L, p.phone, "", cat, reg, "all") for L, p, cat, reg in lets] + [("SCORER", sec.phone, "", "ВК", "Москва", "all")]
    set_panel(cb, pk, pid, rows)
    base = f"/organizer/t/{pk}/conduct/p/{pid}/"
    cb.get(base); f = cb.page.form(action_contains="/draw/"); cb.submit(f, note="draw")
    f = cb.page.form(action_contains="/publish/"); cb.submit(f, note="publish_start")
    t1 = time.time()
    s = {L: judge_all(b_of[p.phone], pk, pid, L, skill=R.uniform(6.2, 7.2), per_mark_share=0.05) for L, p, _, _ in lets}
    print("ride", i + 1, pid, "signed", s, round(time.time() - t1), "s", flush=True)
    print("  scorer", scorer_publish(sb, pk, pid), flush=True)
print("TOTAL", round(time.time() - T0), flush=True)
