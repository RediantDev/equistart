"""Сценарий 4: однодневные клубные соревнования по выездке. 3 судьи на буквах + судья-подсчёт."""
import sys, time
sys.path.insert(0, "/home/claude/sim")
from people import Person, R
from flows import *
from setup_base import ensure_moderator
from orm import classes_of
from tests_data import DRESSAGE_TESTS

SC = "S4 выездка 1 день"
T0 = time.time()
mod = ensure_moderator()
chief = Person(4001, "+79264001001", female=True, level="medium")
j_e = Person(4002, "+79264001002", female=True, level="novice")
j_b = Person(4003, "+79264001003", female=False, level="confident")
sec = Person(4004, "+79264001004", female=True, level="medium")   # главный секретарь — судья-подсчёт
cb, eb, bb, sb = [reg_or_login(SC, p, ["judge"]) for p in (chief, j_e, j_b, sec)]
D = "2026-11-15"
TESTS = {}

def make_tests(b, pk):
    TESTS["pp"] = create_test(b, pk, **DRESSAGE_TESTS["pp_youth"])

spec = dict(title="Кубок клуба «Дубрава» по выездке", start=D, end=D, venue=("КСК «Дубрава»", "len", "Всеволожск", "д. Лесное, 3"),
            contact_name=chief.fio, contact=chief.phone, deadline="2026-11-12", before_programs=make_tests,
            programs=[dict(discipline="dressage", day=D, time="10:00", test=2, templates=[1]),
                      dict(discipline="dressage", day=D, time="12:30", test=lambda: TESTS["pp"], templates=[2, 6, 7])])
pk, progs = create_tournament(cb, spec)
print("tournament", pk, progs, TESTS)
for p in (j_e, j_b, sec):
    invite(cb, pk, p.phone, "judge")
for b in (eb, bb, sb):
    accept_invites(b)
print(moderate(mod, pk))
classes = classes_of(pk)
by_prog = {}
for c in classes:
    by_prog.setdefault(c["program"], []).append(c)
plan = {progs[0]: 9, progs[1]: 16}
idx = 4100
for pid, n in plan.items():
    done = 0
    while done < n:
        c = R.choice(by_prog[pid])
        by = R.randint(c["by_from"], c["by_to"]) if c["by_from"] and c["by_to"] else (R.randint(1975, c["by_to"]) if c["by_to"] else R.randint(1975, 2005))
        a = Person(idx, f"+7926{idx:07d}", birth_year=by); idx += 1
        ab = register(SC, a, ["athlete"])
        aid = fill_own_profile(ab, a, rank_d=R.choice(["none", "3", "2", "1y"]))
        hid, _ = add_horse(ab, age=R.randint(max(c["ha_min"] or 6, 7), 18))
        r = apply_pairs(ab, spec["title"], [(aid, hid)], lambda i: [(c["id"], None)])
        done += 1
print("apps done", round(time.time() - T0))
print("decided", decide_all(cb, pk, reject_share=0.0))
# бригады: C — главный судья, E, B; подсчёт — главный секретарь
for pid in progs:
    set_panel(cb, pk, pid, [("C", chief.phone, "", "1К", "Ленинградская обл.", "all"),
                            ("E", j_e.phone, "", "2К", "Санкт-Петербург", "all"),
                            ("B", j_b.phone, "", "1К", "Санкт-Петербург", "all"),
                            ("SCORER", sec.phone, "", "ВК", "Санкт-Петербург", "all")])
    base = f"/organizer/t/{pk}/conduct/p/{pid}/"
    cb.get(base); f = cb.page.form(action_contains="/draw/"); cb.submit(f, note="draw")
    f = cb.page.form(action_contains="/publish/"); cb.submit(f, note="publish_start")
    s = {L: judge_all(b, pk, pid, L, skill=R.uniform(6.0, 7.3), per_mark_share=0.15) for L, b in (("C", cb), ("E", eb), ("B", bb))}
    print("signed", pid, s)
    print("scorer", scorer_publish(sb, pk, pid))
print("TOTAL", round(time.time() - T0))
