"""Сценарий 2: однодневный конкур, 5 маршрутов, 30 пар, в одном маршруте — 2 пары."""
import sys, time
sys.path.insert(0, "/home/claude/sim")
from people import Person, R
from flows import *
from setup_base import ensure_moderator, IVAN
from orm import classes_of

SC = "S2 конкур 1 день"
T0 = time.time()
mod = ensure_moderator()

judge = Person(201, "+79182000001", female=True, level="medium")
jb = reg_or_login(SC, judge, ["judge"])
D = "2026-11-08"
spec = dict(title="Открытый кубок КСК «Пегас» по конкуру — ноябрь", start=D, end=D, venue=("КСК «Пегас»", "kda", "Краснодар", "ул. Конная, 1"),
            contact_name=judge.fio, contact=judge.phone, deadline="2026-11-05", level="club",
            programs=[
                dict(discipline="jumping", day=D, time="09:00", type="no_time", heights="40, 50", templates=[1, 7]),
                dict(discipline="jumping", day=D, time="10:30", type="clear_speed", heights="70", templates=[1],
                     custom=[{"name": "Любители не выше III разряда", "rank_rule": "never", "rank": "2"}]),
                dict(discipline="jumping", day=D, time="12:00", type="jumpoff", heights="90", templates=[2, 5]),
                dict(discipline="jumping", day=D, time="13:30", type="jumpoff_immediate", heights="100", table="B1", templates=[7]),
                dict(discipline="jumping", day=D, time="14:30", type="two_phases", variant="35.1.5.2", heights="110", templates=[7]),
            ])
pk, progs = create_tournament(jb, spec)
print("tournament", pk, progs, jb.page.messages()[:2])
print(moderate(mod, pk))
classes = classes_of(pk)
by_prog = {}
for c in classes:
    by_prog.setdefault(c["program"], []).append(c)
plan = [8, 9, 7, 2, 4]   # пар на маршрут = 30

def fits(c, by, horse_age):
    if c["by_from"] and by < c["by_from"]: return False
    if c["by_to"] and by > c["by_to"]: return False
    if c["ha_min"] and horse_age < c["ha_min"]: return False
    if c["ha_max"] and horse_age > c["ha_max"]: return False
    return True

# участники: 3 тренера с группами + индивидуальные спортсмены
slots = []  # (program_id, count)
for pid, n in zip(progs, plan):
    slots += [pid] * n
R.shuffle(slots)
groups = [6, 5, 4]  # пары у тренеров = 15, остальные 15 — сами
idx = 210
apps = []
cursor = 0
for gi, gsize in enumerate(groups):
    coach = Person(idx, f"+7918200{idx:04d}", level=["confident", "medium", "novice"][gi]); idx += 1
    cb = reg_or_login(SC, coach, ["coach"], " (тренер)")
    pairs, lines = [], []
    for k in range(gsize):
        pid = slots[cursor]; cursor += 1
        cl = by_prog[pid][0]
        by = cl["by_to"] - 1 if cl["by_to"] else R.choice(range(1985, 2008))
        if cl["by_from"] and cl["by_to"]:
            by = R.randint(cl["by_from"], cl["by_to"])
        a = Person(idx, f"+7918200{idx:04d}", birth_year=by); idx += 1
        aid = add_athlete(cb, a, rank_j=R.choice(["none", "3", "2", "1y"]), coach=coach.fio)
        hid, _ = add_horse(cb, age=R.randint(max(cl["ha_min"] or 6, 6), 15))
        pairs.append((aid, hid)); lines.append([(cl["id"], None)])
    r = apply_pairs(cb, spec["title"], pairs, lambda i: lines[i])
    apps.append(r)
    print("coach", coach.level, r)
while cursor < len(slots):
    pid = slots[cursor]; cursor += 1
    cl = by_prog[pid][0] if R.random() < 0.6 else by_prog[pid][-1]
    by = R.randint(cl["by_from"], cl["by_to"]) if cl["by_from"] and cl["by_to"] else R.choice(range(1980, 2008))
    a = Person(idx, f"+7918200{idx:04d}", birth_year=by); idx += 1
    ab = reg_or_login(SC, a, ["athlete"])
    aid = fill_own_profile(ab, a, rank_j=R.choice(["none", "3", "2"]))
    hid, _ = add_horse(ab, age=R.randint(max(cl["ha_min"] or 6, 6), 16))
    r = apply_pairs(ab, spec["title"], [(aid, hid)], lambda i: [(cl["id"], None)])
    apps.append(r)
print("apps", len([a for a in apps if a]), "lines", sum(a[1] for a in apps if a))
acc, rej = decide_all(jb, pk, reject_share=0.0)
print("decided", acc, rej)
for pid in progs:
    s = conduct_jumping(jb, pk, pid)
    print("conducted", pid, s)
print("done in", round(time.time() - T0), "s")
