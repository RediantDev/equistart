"""Автоматический подсчёт у поля (ТЗ 2.2, раздел 10): конкур (JMP-03…05) и выездка (DRS-03…09).

Формулы — apps/sport/jumping.py и apps/sport/dressage.py. Здесь — хранение ввода, права и пересчёт итогов (Result).
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from apps.core.models import audit
from apps.entries.models import Entry
from apps.entries.services import require_judge
from apps.sport import dressage as D
from apps.sport import jumping as J

from .models import DressageFlags, DressageSheet, JudgePosition, JumpRound, Result, StartSlot

ROUND_STATUS = {
    StartSlot.Status.WITHDRAWN: "withdrawn",
    StartSlot.Status.NO_SHOW: "no_show",
}


def group_key(entry):
    """Классификация: зачёт и высота (в маршруте с несколькими высотами каждая высота — отдельно)."""
    c = entry.tclass
    return (c.merged_into_id or c.pk, entry.height or c.height)


def _entries(program):
    return list(
        Entry.objects.filter(program=program, decision=Entry.Decision.ACCEPTED)
        .select_related("tclass", "athlete", "horse", "application")
        .order_by("pk")
    )


# ======================= КОНКУР =======================

def course(program) -> dict:
    c = dict(program.course or {})
    c.setdefault("rounds", {})
    c.setdefault("indoor", False)
    c.setdefault("timing", "electronic")
    return c


def round_params(program, key) -> dict:
    """Норма и лимит времени гита (JMP-01)."""
    c = course(program)
    rp = c["rounds"].get(key, {})
    rt = program.route
    table = J.table_for(rt, program.variant, program.table, key)
    ta = rp.get("time_allowed")
    return {
        "time_allowed": Decimal(str(ta)) if ta not in (None, "") else None,
        "limit": J.time_limit(table, ta, rp.get("length")),
        "table": table,
        "length": rp.get("length"),
        "speed": rp.get("speed"),
    }


def save_course(user, program, data: dict) -> dict:
    """Параметры маршрута в день старта: длина, скорость, норма времени по гитам, хронометраж, помещение."""
    require_judge(user, program.tournament)
    if not program.is_jumping or program.route.manual:
        raise ValueError("Параметры задаются для маршрутов с автоматическим подсчётом.")
    c = course(program)
    for spec in J.rounds_of(program.route, program.variant):
        k = spec.key
        rp = {}
        for f in ("length", "speed", "time_allowed"):
            raw = str(data.get(f"{k}_{f}", "")).strip().replace(",", ".")
            if raw == "":
                continue
            try:
                v = int(Decimal(raw)) if f != "time_allowed" else Decimal(raw)
            except Exception:
                raise ValueError("Длина, скорость и норма времени — числа.")
            if v <= 0:
                raise ValueError("Значения должны быть больше нуля.")
            rp[f] = int(v) if f != "time_allowed" else str(v.quantize(Decimal("0.01")) if v != v.to_integral_value() else int(v))
        if "time_allowed" not in rp and rp.get("length") and rp.get("speed"):
            rp["time_allowed"] = str(J.suggest_time_allowed(rp["length"], rp["speed"]))
        c["rounds"][k] = rp
    c["indoor"] = bool(data.get("indoor"))
    c["timing"] = "manual" if data.get("timing") == "manual" else "electronic"
    if data.get("obstacles"):
        try:
            c["obstacles"] = int(data["obstacles"])
        except ValueError:
            raise ValueError("Число препятствий — целое число.")
    program.course = c
    program.save(update_fields=["course"])
    audit(user, "program.course", program, course=c)
    recompute(program)
    return c


def _inputs(entry) -> dict:
    return {r.round: r for r in entry.jump_rounds.all()}


def _score(program, key, jr: JumpRound) -> J.RoundResult:
    rt = program.route
    spec = J.route_round(rt, key)
    spec = J.RoundSpec(spec.key, spec.title, J.round_timed(rt, program.variant, key), spec.jumpoff)
    rp = round_params(program, key)
    inp = J.RoundInput(knockdowns=jr.knockdowns, refusals=jr.refusals, corrections=jr.corrections, time=jr.time, fall=jr.fall,
                       status=jr.status, reason=jr.reason)
    return J.score_round(rp["table"], spec, inp, rp["time_allowed"], rp["limit"], course(program)["indoor"])


def standings(program) -> dict:
    """{(зачёт, высота): [Standing]} с местами по формуле маршрута."""
    rt = program.route
    groups = {}
    entries = _entries(program)
    slots = {s.entry_id: s for s in StartSlot.objects.filter(program=program)}
    for e in entries:
        st = J.Standing(e)
        rounds = _inputs(e)
        for spec in J.rounds_of(rt, program.variant):
            jr = rounds.get(spec.key)
            if jr is not None:
                try:
                    st.rounds[spec.key] = _score(program, spec.key, jr)
                except ValueError:
                    pass
        slot = slots.get(e.pk)
        first = J.rounds_of(rt, program.variant)[0].key
        if first not in st.rounds and slot and slot.status in ROUND_STATUS:
            st.rounds[first] = J.RoundResult(status=ROUND_STATUS[slot.status], reason=slot.get_status_display())
        groups.setdefault(group_key(e), []).append(st)
    for key, items in groups.items():
        J.rank(rt, items, program.variant)
    return groups


def next_stage_candidates(program, stage) -> list:
    """Кто проходит в следующий гит (перепрыжку, 2-ю фазу, 2-й гит) — по каждой классификации отдельно."""
    out = []
    for items in standings(program).values():
        out += J.jumpoff_candidates(program.route, items, stage)
    return out


def stage_allowed(program, entry, stage) -> str | None:
    """Почему нельзя вводить этот гит для пары (None — можно)."""
    rt = program.route
    keys = [r.key for r in J.rounds_of(rt, program.variant)]
    if stage not in keys:
        return "В этом маршруте нет такого гита."
    if stage == keys[0]:
        return None
    if entry not in next_stage_candidates(program, stage):
        return "Пара не проходит в этот гит по результатам предыдущего."
    return None


@transaction.atomic
def save_round(user, entry_id, stage, data: dict, client_key: str = "") -> JumpRound:
    e = Entry.objects.select_for_update(of=("self",)).select_related("program__tournament").get(pk=entry_id)
    program = e.program
    require_judge(user, program.tournament)
    if not program.is_jumping or program.route.manual:
        raise ValueError("Маршрут с ручным вводом — используйте форму результата.")
    if e.decision != Entry.Decision.ACCEPTED:
        raise ValueError("Выступление не принято.")
    existing = JumpRound.objects.filter(entry=e, round=stage).first()
    if existing and client_key and existing.client_key == client_key:
        return existing  # повтор отправки с устройства (SCR-02) — не дублируем
    why = stage_allowed(program, e, stage)
    if why and not existing:
        raise ValueError(why)

    def num(name, lo=0, hi=20):
        raw = str(data.get(name, "0") or "0").strip()
        try:
            v = int(raw)
        except ValueError:
            raise ValueError("Повалы, неповиновения и коррекции — целые числа.")
        if not lo <= v <= hi:
            raise ValueError("Проверьте число повалов и неповиновений.")
        return v

    status = data.get("status") or "ok"
    if status not in JumpRound.Status.values:
        raise ValueError("Неизвестный статус.")
    t_raw = str(data.get("time", "") or "").strip().replace(",", ".")
    t = None
    if t_raw:
        try:
            t = Decimal(t_raw)
        except Exception:
            raise ValueError("Время — число секунд, например 38,41.")
        if t <= 0 or t > 999:
            raise ValueError("Проверьте время.")
        digits = 1 if course(program)["timing"] == "manual" else 2
        t = t.quantize(Decimal(10) ** -digits)
    jr = existing or JumpRound(entry=e, round=stage)
    old = {"knockdowns": jr.knockdowns, "refusals": jr.refusals, "time": str(jr.time) if jr.time is not None else None,
           "status": jr.status} if existing else None
    jr.knockdowns, jr.refusals, jr.corrections = num("knockdowns"), num("refusals", 0, 3), num("corrections", 0, 5)
    jr.time, jr.fall, jr.status = t, bool(data.get("fall")), status
    jr.reason = (data.get("reason") or "").strip()[:200]
    jr.client_key = client_key[:64]
    jr.updated_by = user
    if existing:
        jr.revision += 1
    # проверка: если таблица требует время — без него не сохраняем законченный гит
    if jr.status == "ok" and not jr.fall:
        _score(program, stage, jr)
    jr.save()
    slot = getattr(e, "start_slot", None)
    if slot and stage == J.rounds_of(program.route, program.variant)[0].key and slot.status in (StartSlot.Status.WAITING, StartSlot.Status.STARTED):
        slot.status = StartSlot.Status.FINISHED
        slot.save(update_fields=["status", "updated_at"])
    audit(user, "round.save", e, round=stage, knockdowns=jr.knockdowns, refusals=jr.refusals, time=str(jr.time) if jr.time else None,
          status=jr.status, fall=jr.fall, before=old)
    recompute(program)
    return jr


def jumping_row_values(program, st: J.Standing) -> dict:
    out = {"rounds": {}, "total": J.fmt(st.total) if st.total is not None else ""}
    for spec in J.rounds_of(program.route, program.variant):
        r = st.rounds.get(spec.key)
        if r is None:
            continue
        out["rounds"][spec.key] = {
            "title": spec.title, "status": r.status, "reason": r.reason,
            "pen": J.fmt(r.penalties) if r.finished else "", "time": J.fmt(r.time) if r.time is not None else "",
            "obstacle_pen": J.fmt(r.obstacle_pen), "time_pen": J.fmt(r.time_pen),
        }
    return out


# ======================= ВЫЕЗДКА =======================

def panel(program) -> list[JudgePosition]:
    return list(program.judge_positions.select_related("user").order_by("id"))


def letters(program) -> list[JudgePosition]:
    return [p for p in panel(program) if not p.is_scorer]


def my_position(user, program, letter=None) -> JudgePosition | None:
    qs = [p for p in panel(program) if p.user_id == user.pk and not p.is_scorer]
    if letter:
        qs = [p for p in qs if p.letter == letter]
    return qs[0] if qs else None


def is_scorer(user, program) -> bool:
    return any(p.is_scorer and p.user_id == user.pk for p in panel(program))


def test_of(program) -> dict:
    t = program.test_snapshot
    if not t:
        raise ValueError("Для езды не выбран лист.")
    return t


def _blank_marks(test) -> dict:
    return {p["key"]: [None] * len(D.items_of(p)) for p in test["parts"]}


def sheet_for(entry, position) -> DressageSheet:
    sh, _ = DressageSheet.objects.get_or_create(entry=entry, position=position, defaults={"marks": _blank_marks(test_of(entry.program))})
    return sh


@transaction.atomic
def save_mark(user, entry_id, letter, part_key, index, value, comment=None) -> DressageSheet:
    e = Entry.objects.select_related("program__tournament").get(pk=entry_id)
    pos = my_position(user, e.program, letter)
    if not pos:
        raise PermissionDenied
    if e.decision != Entry.Decision.ACCEPTED:
        raise ValueError("Выступление не принято.")
    test = test_of(e.program)
    part = next((p for p in test["parts"] if p["key"] == part_key), None)
    if not part or pos.role not in ("all", part_key):
        raise ValueError("Эту часть листа оценивает другой судья.")
    items = D.items_of(part)
    if not 0 <= index < len(items):
        raise ValueError("Нет такого пункта листа.")
    sh = DressageSheet.objects.select_for_update().filter(entry=e, position=pos).first() or sheet_for(e, pos)
    if sh.signed:
        raise ValueError("Лист подписан. Исправить оценку можно, только если судья-подсчёт вернёт лист.")
    marks = sh.marks or _blank_marks(test)
    row = list(marks.get(part_key) or [None] * len(items))
    old = row[index]
    if value in (None, ""):
        row[index] = None
    else:
        row[index] = str(D.check_mark(value, str(part.get("step", "0.5"))))
    marks[part_key] = row
    sh.marks = marks
    if comment is not None:
        c = dict(sh.comments or {})
        text = comment.strip()[:300]
        if text:
            c[f"{part_key}:{index}"] = text
        else:
            c.pop(f"{part_key}:{index}", None)
        sh.comments = c
    sh.save()
    if old != row[index]:
        audit(user, "dressage.mark", e, position=pos.letter, part=part_key, item=index + 1, before=old, after=row[index])
    return sh


def sheet_missing(test, position, sheet) -> list[str]:
    out = []
    for p in test["parts"]:
        if position.role not in ("all", p["key"]):
            continue
        row = (sheet.marks or {}).get(p["key"]) or []
        items = D.items_of(p)
        for i, it in enumerate(items):
            if i >= len(row) or row[i] in (None, ""):
                out.append(str(it.get("n") or it.get("text", "")[:30]))
    return out


@transaction.atomic
def sign(user, entry_id, letter, remark="") -> DressageSheet:
    e = Entry.objects.select_related("program__tournament").get(pk=entry_id)
    pos = my_position(user, e.program, letter)
    if not pos:
        raise PermissionDenied
    sh = DressageSheet.objects.select_for_update().get(entry=e, position=pos)
    if sh.signed:
        return sh
    miss = sheet_missing(test_of(e.program), pos, sh)
    if miss:
        raise ValueError(f"Не все оценки проставлены: {', '.join(miss[:8])}{'…' if len(miss) > 8 else ''}.")
    sh.remark = (remark or sh.remark or "").strip()[:2000]
    sh.signed_at, sh.signed_by, sh.returned_reason = timezone.now(), user, ""
    sh.save()
    audit(user, "dressage.sign", e, position=pos.letter, marks=sh.marks)
    slot = getattr(e, "start_slot", None)
    if slot and slot.status in (StartSlot.Status.WAITING, StartSlot.Status.STARTED):
        slot.status = StartSlot.Status.FINISHED
        slot.save(update_fields=["status", "updated_at"])
    recompute(e.program)
    return sh


@transaction.atomic
def return_sheet(user, entry_id, letter, reason) -> DressageSheet:
    e = Entry.objects.select_related("program__tournament").get(pk=entry_id)
    if not is_scorer(user, e.program):
        raise PermissionDenied
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите причину возврата — её увидит судья.")
    sh = DressageSheet.objects.select_for_update().get(entry=e, position__program=e.program, position__letter=letter)
    sh.signed_at, sh.signed_by, sh.returned_reason = None, None, reason[:300]
    sh.revision += 1
    sh.save()
    DressageFlags.objects.filter(entry=e).update(approved_at=None, approved_by=None)
    audit(user, "dressage.return", e, position=letter, reason=reason)
    from apps.core.models import notify

    notify([sh.position.user], f"Лист возвращён: {e.athlete_snapshot.get('name')} / {e.horse_snapshot.get('name')} — {reason}",
           f"/organizer/t/{e.tournament_id}/conduct/p/{e.program_id}/judge/{letter}/?entry={e.pk}", "dressage")
    recompute(e.program)
    return sh


@transaction.atomic
def set_flags(user, entry_id, errors=None, technical=None, eliminated=None, reason=None) -> DressageFlags:
    """DRS-05: ошибки в схеме, технические нарушения и исключение отмечает судья на C (или судья-подсчёт)."""
    e = Entry.objects.select_related("program__tournament").get(pk=entry_id)
    pos = my_position(user, e.program, "C")
    if not pos and not is_scorer(user, e.program):
        raise PermissionDenied
    f, _ = DressageFlags.objects.select_for_update().get_or_create(entry=e)
    if f.approved_at:
        raise ValueError("Результат уже утверждён — сначала судья-подсчёт должен вернуть лист.")
    before = (f.errors, f.technical, f.eliminated)
    if errors is not None:
        f.errors = max(0, min(5, int(errors)))
    if technical is not None:
        f.technical = max(0, min(10, int(technical)))
    if eliminated is not None:
        f.eliminated = bool(eliminated)
        f.reason = (reason or "").strip()[:200] if f.eliminated else ""
    test = test_of(e.program)
    if (f.errors or f.technical) and not test.get("penalty_scheme"):
        raise ValueError("Для езды не выбрана схема штрафов — укажите её в карточке езды (организатор).")
    f.save()
    audit(user, "dressage.flags", e, before=before, after=(f.errors, f.technical, f.eliminated), reason=f.reason)
    recompute(e.program)
    return f


def pair_scores(program) -> dict:
    """{entry: (PairResult | None, {letter: sheet}, flags)}: итог только когда все листы подписаны."""
    test = test_of(program)
    poss = letters(program)
    out = {}
    sheets = {}
    for sh in DressageSheet.objects.filter(position__program=program).select_related("position"):
        sheets.setdefault(sh.entry_id, {})[sh.position.letter] = sh
    flags = {f.entry_id: f for f in DressageFlags.objects.filter(entry__program=program)}
    for e in _entries(program):
        my = sheets.get(e.pk, {})
        fl = flags.get(e.pk)
        errors, technical = (fl.errors, fl.technical) if fl else (0, 0)
        if fl and fl.eliminated:
            out[e] = (D.PairResult([], None, True), my, fl)
            continue
        if not poss or any(not (my.get(p.letter) and my[p.letter].signed) for p in poss):
            out[e] = (None, my, fl)
            continue
        js = []
        for p in poss:
            js.append(D.judge_score(test, p.letter, p.role, {k: [Decimal(x) for x in v] for k, v in my[p.letter].marks.items()}, errors, technical))
        out[e] = (D.pair_result(test, js, errors, technical), my, fl)
    return out


@transaction.atomic
def approve(user, entry_id) -> DressageFlags:
    e = Entry.objects.select_related("program__tournament").get(pk=entry_id)
    if not is_scorer(user, e.program):
        raise PermissionDenied
    res, sheets, _ = pair_scores(e.program).get(e, (None, {}, None))
    f, _ = DressageFlags.objects.get_or_create(entry=e)
    if res is None:
        raise ValueError("Ещё не все судьи подписали листы.")
    f.approved_at, f.approved_by = timezone.now(), user
    f.save(update_fields=["approved_at", "approved_by", "updated_at"])
    audit(user, "dressage.approve", e)
    recompute(e.program)
    return f


@transaction.atomic
def set_norm(user, entry_id, norm) -> DressageFlags:
    e = Entry.objects.select_related("program__tournament").get(pk=entry_id)
    if not is_scorer(user, e.program):
        raise PermissionDenied
    f, _ = DressageFlags.objects.get_or_create(entry=e)
    f.norm = (norm or "").strip()[:20]
    f.save(update_fields=["norm", "updated_at"])
    audit(user, "dressage.norm", e, norm=f.norm)
    recompute(e.program)
    return f


# ======================= ПЕРЕСЧЁТ ИТОГОВ =======================

def recompute(program):
    """Пересчитать Result по формуле элемента программы (RES-01). Прочий маршрут не трогаем — его вводит судья."""
    if program.is_jumping and not program.route.manual:
        return _recompute_jumping(program)
    if program.is_dressage and program.test_snapshot:
        return _recompute_dressage(program)


def _save_result(e, place, status, values, sort):
    r, _ = Result.objects.get_or_create(entry=e)
    r.place, r.status, r.values, r.auto, r.sort_key = place, status, values, True, sort
    r.save()


STATUS_MAP = {"eliminated": Result.Status.ELIMINATED, "retired": Result.Status.RETIRED, "withdrawn": Result.Status.WITHDRAWN,
              "no_show": Result.Status.NO_SHOW}


def _recompute_jumping(program):
    for items in standings(program).values():
        for st in items:
            if st.status == "pending" and not st.rounds:
                Result.objects.filter(entry=st.key, auto=True).delete()
                continue
            status = STATUS_MAP.get(st.status, "") if st.place is None else ""
            _save_result(st.key, st.place, status, jumping_row_values(program, st), [])


def _recompute_dressage(program):
    test = test_of(program)
    scores = pair_scores(program)
    by_group = {}
    for e, (res, sheets, fl) in scores.items():
        by_group.setdefault(group_key(e), []).append((e, res, sheets, fl))
    poss = letters(program)
    for items in by_group.values():
        done = [(e, res) for e, res, _, _ in items if res is not None]
        places = D.rank(done, test.get("tie_rule") or "share")
        judge_pl = {p.letter: D.judge_places(done, p.letter) for p in poss}
        for e, res, sheets, fl in items:
            if res is None:
                signed = sum(1 for p in poss if sheets.get(p.letter) and sheets[p.letter].signed)
                if not signed and not fl:
                    Result.objects.filter(entry=e, auto=True).delete()
                    continue
                _save_result(e, None, "", {"pending": True, "signed": signed, "of": len(poss)}, [])
                continue
            judges = {}
            for j in res.judges:
                judges[j.position] = {"points": J.fmt(j.points), "pct": f"{D.q3(j.pct):.3f}".replace(".", ","), "place": judge_pl[j.position].get(e)}
            values = {
                "judges": judges, "order": [p.letter for p in poss],
                "total_points": J.fmt(res.total_points), "pct": f"{D.q3(res.pct):.3f}".replace(".", ",") if res.pct is not None else "",
                "errors": fl.errors if fl else 0, "technical": fl.technical if fl else 0, "norm": fl.norm if fl else "",
                "approved": bool(fl and fl.approved_at),
            }
            status = Result.Status.ELIMINATED if res.eliminated else ""
            _save_result(e, places.get(e), status, values, [])


# ======================= ПРОТОКОЛ: КОЛОНКИ И ЯЧЕЙКИ =======================

SUM_ROUTES = ("two_rounds", "two_phases_special")


def result_columns(program) -> list[str]:
    """Заголовки колонок результата для протокола (JMP-09, DRS-11)."""
    from apps.tournaments.disciplines import RESULT_FIELDS

    if program.is_jumping and not program.route.manual:
        cols = []
        for spec in J.rounds_of(program.route, program.variant):
            short = {"main": "", "jo1": "перепр.", "jo2": "2-я перепр.", "phase1": "1-я фаза", "phase2": "2-я фаза",
                     "round1": "1-й гит", "round2": "2-й гит"}.get(spec.key, spec.title)
            cols += [f"Ш/о {short}".strip(), f"Время {short}".strip()]
        if program.route.key in SUM_ROUTES:
            cols.append("Итого ш/о")
        return cols
    if program.is_dressage and program.test_snapshot:
        cols = []
        for p in letters(program):
            cols += [f"{p.letter}: баллы", f"{p.letter}: %", f"{p.letter}: место"]
        return cols + ["Ошибки", "Всего баллов", "%", "Норматив"]
    return [RESULT_FIELDS.get(f, f) for f in program.result_fields]


def result_cells(program, result) -> list:
    v = result.values or {}
    if program.is_jumping and not program.route.manual:
        out = []
        for spec in J.rounds_of(program.route, program.variant):
            r = (v.get("rounds") or {}).get(spec.key)
            if not r:
                out += ["", ""]
            elif r["status"] != "ok":
                out += [r.get("reason") or {"eliminated": "искл.", "retired": "сошёл", "withdrawn": "снят", "no_show": "н/с"}.get(r["status"], ""), ""]
            else:
                out += [r["pen"], r["time"]]
        if program.route.key in SUM_ROUTES:
            out.append(v.get("total", ""))
        return out
    if program.is_dressage and program.test_snapshot:
        if v.get("pending"):
            return []
        out = []
        for p in letters(program):
            j = (v.get("judges") or {}).get(p.letter) or {}
            out += [j.get("points", ""), j.get("pct", ""), j.get("place") or ""]
        errs = v.get("errors") or 0
        return out + [errs or "", v.get("total_points", ""), v.get("pct", ""), v.get("norm", "")]
    return [v.get(f) for f in program.result_fields]
