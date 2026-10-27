"""Сценарий 1: конкур, 4 дня, 20 маршрутов, ~400 спортивных пар. Судья регистрируется и создаёт турнир с нуля."""
import sys, time, json
sys.path.insert(0, "/home/claude/sim")
from people import Person, R
from flows import *
from setup_base import ensure_moderator
from orm import classes_of, q
import stables_flows as SF

SC = "S1 конкур 4 дня"
T0 = time.time()
mod = ensure_moderator()

judge = Person(1001, "+79261001001", female=False, level="confident")   # главный судья: создаёт турнир
judge2 = Person(1002, "+79261001002", female=True, level="novice")      # второй судья, плохо знаком с сайтом
stable = Person(1003, "+79261001003", female=True, level="medium")
jb = register(SC, judge, ["judge"])
j2b = register(SC, judge2, ["judge"])
sb = register(SC, stable, ["club"])

days = ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22"]
# 20 маршрутов: (день, тип, высоты, таблица, вариант, шаблоны зачётов, свой зачёт, план пар)
ROUTES = [
    (0, "no_time", "50, 60", "", "", [1, 7], None, 40),
    (0, "clear_speed", "80", "", "", [1, 6, 7], None, 45),
    (0, "clear_speed", "100", "", "", [2, 5], None, 38),
    (0, "jumpoff", "115", "", "", [3, 5], None, 30),
    (0, "jumpoff", "130", "", "", [7], None, 18),
    (1, "no_time_jo", "70", "", "", [1, 7], None, 42),
    (1, "jumpoff_immediate", "90", "B1", "", [1, 2, 7], None, 40),
    (1, "two_phases", "105", "", "35.1.5.2", [2, 5], None, 36),
    (1, "clear_speed", "120", "", "", [3, 5], None, 28),
    (1, "speed_handiness", "110", "C", "", [7], None, 16),
    (2, "clear_speed", "60, 70, 80", "", "", [1, 7], {"name": "Любители 18+ не выше II разряда", "rank_rule": "never", "rank": "2"}, 50),
    (2, "jumpoff", "100", "", "", [2, 5, 6], None, 40),
    (2, "two_phases_special", "110", "B2", "", [3, 5], None, 30),
    (2, "two_rounds", "125", "", "34.3.1", [7], None, 16),
    (2, "jumpoff_2", "135", "", "", [7], None, 12),
    (3, "no_time", "60", "", "", [8, 1], None, 24),
    (3, "clear_speed", "90", "", "", [1, 2, 6], None, 44),
    (3, "jumpoff_immediate", "110", "B1", "", [2, 3, 5], None, 36),
    (3, "jumpoff", "120", "", "", [5, 7], None, 26),
    (3, "jumpoff", "140", "", "", [7], {"name": "Гран-при: Взрослые МС/КМС", "rank_rule": "min", "rank": "kms"}, 14),
]
programs = []
for i, (d, typ, hts, table, var, tpls, custom, _) in enumerate(ROUTES):
    programs.append(dict(discipline="jumping", day=days[d], time=f"{8 + (i % 5) * 2:02d}:00", type=typ, heights=hts, table=table, variant=var,
                         templates=tpls, custom=[custom] if custom else [],
                         name="Гран-при" if i == 19 else ""))
spec = dict(title="Всероссийские соревнования «Осенний Гран-при» по конкуру", start=days[0], end=days[-1],
            venue=("КСК «Новый Век»", "mos", "Москва", "Новорижское ш., 9 км"), contact_name=judge.fio, contact=judge.phone,
            deadline="2026-11-16", level="regional", classification=True, programs=programs,
            cancel_terms="Отказ от старта — не позднее 18:00 накануне дня маршрута, через сайт или судье.")
t1 = time.time()
pk, progs = create_tournament(jb, spec)
print("tournament", pk, len(progs), "programs; wizard", round(time.time() - t1), "s")
for ph, role in [(judge2.phone, "judge"), (stable.phone, "stable")]:
    print("invite", invite(jb, pk, ph, role)[:1])
print("accepted", accept_invites(j2b), accept_invites(sb))
print(moderate(mod, pk))

classes = classes_of(pk)
by_prog = {}
for c in classes:
    by_prog.setdefault(c["program"], []).append(c)
plan = {pid: r[7] for pid, r in zip(progs, ROUTES)}
json.dump(dict(pk=pk, progs=progs, plan=plan), open("/home/claude/sim/s1_meta.json", "w"))

RANK_ORDER = ["none", "3y", "2y", "1y", "3", "2", "1", "kms", "ms", "msmk"]


def eligible(c, by, horse_age, rank):
    if c["by_from"] and by < c["by_from"]: return False
    if c["by_to"] and by > c["by_to"]: return False
    if c["ha_min"] and horse_age < c["ha_min"]: return False
    if c["ha_max"] and horse_age > c["ha_max"]: return False
    if c["kind"] == "pony": return False
    if c["rank_rule"] == "min" and RANK_ORDER.index(rank or "none") < RANK_ORDER.index(c["rank"]): return False
    if c["rank_rule"] == "never" and RANK_ORDER.index(rank or "none") >= RANK_ORDER.index(c["rank"]): return False
    return True


# слоты стартов по маршрутам
need = dict(plan)
N_PAIRS = 400


def make_pair():
    """Возраст всадника и лошади — так, чтобы распределение походило на реальные старты."""
    kind = R.choices(["child", "youth", "junior", "adult", "pro"], [22, 18, 15, 30, 15])[0]
    by = {"child": R.randint(2012, 2014), "youth": R.randint(2009, 2011), "junior": R.randint(2005, 2008),
          "adult": R.randint(1975, 2004), "pro": R.randint(1980, 2003)}[kind]
    rank = {"child": R.choice(["none", "3y", "2y"]), "youth": R.choice(["3y", "2y", "1y", "3"]), "junior": R.choice(["1y", "3", "2", "1"]),
            "adult": R.choice(["none", "3", "2", "1"]), "pro": R.choice(["1", "kms", "kms", "ms"])}[kind]
    hage = R.randint(6, 17)
    return by, rank, hage


def pick_lines(by, rank, hage):
    """Пара стартует 1–3 раза за турнир: подбираем маршруты, где ещё нужны пары и есть подходящий зачёт."""
    cands = []
    for pid in progs:
        if need[pid] <= 0:
            continue
        cl = [c for c in by_prog[pid] if eligible(c, by, hage, rank)]
        if cl:
            cands.append((pid, R.choice(cl)))
    R.shuffle(cands)
    k = R.choices([1, 2, 3], [45, 40, 15])[0]
    chosen, used_days = [], set()
    for pid, c in cands:
        if c["day"] in used_days and R.random() < 0.7:
            continue
        chosen.append((c["id"], None)); used_days.add(c["day"]); need[pid] -= 1
        if len(chosen) >= k:
            break
    return chosen


idx = 1100
COACHES = []
apps_ok, lines_total, pairs_total = 0, 0, 0
# тренеры с группами (~240 пар)
coach_sizes = [R.randint(5, 18) for _ in range(22)]
for gi, gsize in enumerate(coach_sizes):
    coach = Person(idx, f"+7926{idx:07d}"); idx += 1
    cb = register(SC, coach, ["coach"], " (тренер)")
    COACHES.append((coach, cb, []))
    if cb is None:
        continue
    pairs, lines = [], []
    riders = []
    for k in range(gsize):
        by, rank, hage = make_pair()
        lns = pick_lines(by, rank, hage)
        if not lns:
            continue
        # иногда один всадник на двух лошадях
        if riders and R.random() < 0.25:
            aid = R.choice(riders)
        else:
            a = Person(idx, f"+7926{idx:07d}", birth_year=by); idx += 1
            aid = add_athlete(cb, a, rank_j=rank, coach=coach.fio)
            riders.append(aid)
        hid, _ = add_horse(cb, age=hage)
        if aid and hid:
            pairs.append((aid, hid)); lines.append(lns)
            COACHES[-1][2].append(hid)
    if pairs:
        r = apply_pairs(cb, spec["title"], pairs, lambda i: lines[i])
        if r:
            apps_ok += 1; lines_total += r[1]; pairs_total += len(pairs)
    print(f"coach {gi} {coach.level}: pairs {len(pairs)} -> {r}", flush=True)

# индивидуальные спортсмены до ~400 пар
while pairs_total < N_PAIRS and sum(max(v, 0) for v in need.values()) > 0:
    by, rank, hage = make_pair()
    lns = pick_lines(by, rank, hage)
    if not lns:
        continue
    a = Person(idx, f"+7926{idx:07d}", birth_year=by); idx += 1
    ab = register(SC, a, ["athlete"])
    if ab is None:
        continue
    aid = fill_own_profile(ab, a, rank_j=rank)
    hid, _ = add_horse(ab, age=hage)
    pairs = [(aid, hid)]; lines = [lns]
    if R.random() < 0.12:  # вторая лошадь
        hid2, _ = add_horse(ab, age=R.randint(6, 15))
        l2 = pick_lines(by, rank, 10)
        if hid2 and l2:
            pairs.append((aid, hid2)); lines.append(l2)
    if aid and hid:
        r = apply_pairs(ab, spec["title"], pairs, lambda i: lines[i])
        if r:
            apps_ok += 1; lines_total += r[1]; pairs_total += len(pairs)
    if pairs_total % 25 == 0:
        print("pairs", pairs_total, flush=True)

print("applications", apps_ok, "pairs", pairs_total, "starts", lines_total, "time", round(time.time() - T0), flush=True)

# ---------- денники и услуги ----------
t1 = time.time()
d0, d1 = SF.setup(sb, pk, days[0], days[-1])
bk = {"ok": 0, "fail": []}
required_horse = None
for ci, (coach, cb2, hids) in enumerate(COACHES[:14]):
    cb2 = login(SC, coach, " (тренер)")
    for k, h in enumerate(hids):
        cat = "Капитальная конюшня" if k % 3 else "Летник (временная конюшня)"
        req = (ci == 0 and k == 0)
        r = SF.book(cb2, pk, h, cat, d0, d1, required=req)
        if req:
            required_horse = (coach, h)
        if r is True:
            bk["ok"] += 1
        else:
            bk["fail"].append(str(r)[:120])
        if R.random() < 0.5:
            SF.order(cb2, pk, "Опилки", R.randint(2, 6), horse_id=h)
        if k == 0 and R.random() < 0.4:
            SF.order(cb2, pk, "Электричество для коневоза", 4)
print("bookings", bk["ok"], "failed", len(bk["fail"]), bk["fail"][:3], flush=True)
# гонка за последние денники блока жеребцов (5 мест): 4 занимают, затем 6 человек одновременно жмут на последний
racers = [login(SC, c, " (тренер)") for c, _, h in COACHES[14:20] if h]
racer_horses = [h[0] for c, _, h in COACHES[14:20] if h]
for c, _, h in COACHES[20:22]:
    if len(h) >= 2:
        b2 = login(SC, c, " (тренер)")
        for hh in h[:2]:
            SF.book(b2, pk, hh, "Денник для жеребцов", d0, d1)
race = SF.race_last_box(racers, pk, racer_horses, "Денник для жеребцов", d0, d1)
print("race last box: successes", sum(race), "of", len(race), flush=True)
# конюшня: подтверждение, номера, выдача; уменьшить вместимость ниже подтверждённых — должна быть понятная ошибка
st = SF.staff_process(sb, pk, reject_share=0.04)
print("stable staff", st, flush=True)
json.dump(dict(pk=pk, progs=progs, plan=plan, required_horse=required_horse[1] if required_horse else None,
               required_coach=required_horse[0].idx if required_horse else None), open("/home/claude/sim/s1_meta.json", "w"))
print("stables time", round(time.time() - t1), flush=True)
# решения: главный судья
t1 = time.time()
acc, rej = decide_all(jb, pk, reject_share=0.02)
print("decided", acc, rej, round(time.time() - t1), "s", flush=True)
# проведение: дни 1–2 — главный судья, дни 3–4 — второй судья
for i, pid in enumerate(progs):
    who = jb if ROUTES[i][0] < 2 else j2b
    t1 = time.time()
    s = conduct_jumping(who, pk, pid)
    print("route", i + 1, pid, s, round(time.time() - t1), "s", flush=True)
print("TOTAL", round(time.time() - T0), "s")
