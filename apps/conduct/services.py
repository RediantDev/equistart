"""Проведение: порядок стартов, проверка интервалов, публикация протоколов, результаты."""

import datetime
import random

from django.db import transaction
from django.db.models import Max
from django.urls import reverse

from apps.core.models import audit, notify
from apps.entries.models import Entry
from apps.entries.services import require_judge
from apps.tournaments.models import ProgramItem, TournamentClass
from apps.tournaments.services import can_manage

from . import scoring
from .models import Result, ResultPublication, StartListPublication, StartSlot


def eligible(entry) -> bool:
    """В порядок попадают принятые выступления (и с подтверждённым обязательным денником)."""
    if entry.decision != Entry.Decision.ACCEPTED:
        return False
    try:
        from apps.stables.services import stable_dependency_ok
    except ImportError:
        return True
    return stable_dependency_ok(entry)


def entry_height(entry):
    p = entry.program
    if entry.height:
        return entry.height
    if entry.tclass_id and entry.tclass.height:
        return entry.tclass.height
    return max(p.heights) if p.heights else None


def why_not_eligible(entry) -> str:
    if entry.decision != Entry.Decision.ACCEPTED:
        return entry.get_decision_display()
    return "Нет подтверждённого денника"


def _lock_program(user, program_id):
    p = ProgramItem.objects.select_for_update().select_related("tournament").get(pk=program_id)
    require_judge(user, p.tournament)
    return p


def _recompute_times(program):
    slots = list(program.slots.order_by("position", "id"))
    for i, s in enumerate(slots, start=1):
        s.position = i
        if program.start_time:
            base = datetime.datetime.combine(program.day, program.start_time)
            s.start_time = (base + datetime.timedelta(minutes=(i - 1) * program.slot_minutes)).time()
        else:
            s.start_time = None
        s.save(update_fields=["position", "start_time", "updated_at"])


@transaction.atomic
def draw(user, program_id) -> int:
    """Жеребьёвка: добавляет недостающих и случайно перемешивает ещё не стартовавших. Начатые остаются на местах."""
    p = _lock_program(user, program_id)
    existing = {s.entry_id for s in p.slots.all()}
    new = [e for e in Entry.objects.filter(program=p) if e.pk not in existing and eligible(e)]
    top = p.slots.aggregate(m=Max("position"))["m"] or 0
    for i, e in enumerate(new, start=1):
        StartSlot.objects.create(program=p, entry=e, position=top + i)
    slots = list(p.slots.order_by("position"))
    movable = [s for s in slots if s.status == StartSlot.Status.WAITING]
    positions = [s.position for s in movable]
    random.shuffle(movable)
    if len(p.heights) > 1:
        movable.sort(key=lambda s: entry_height(s.entry) or 0)  # блоки по высотам, внутри блока — жребий (JMP-02)
    for pos, s in zip(sorted(positions), movable):
        s.position = pos
        s.save(update_fields=["position"])
    _recompute_times(p)
    audit(user, "startlist.draw", p, added=len(new))
    return len(new)


@transaction.atomic
def add_missing(user, program_id) -> int:
    """Добавить в конец порядка принятых, которых в нём ещё нет (без перемешивания)."""
    p = _lock_program(user, program_id)
    existing = {s.entry_id for s in p.slots.all()}
    new = [e for e in Entry.objects.filter(program=p) if e.pk not in existing and eligible(e)]
    top = p.slots.aggregate(m=Max("position"))["m"] or 0
    for i, e in enumerate(new, start=1):
        StartSlot.objects.create(program=p, entry=e, position=top + i)
    _recompute_times(p)
    if new:
        audit(user, "startlist.add", p, added=len(new))
    return len(new)


@transaction.atomic
def move(user, slot_id, new_position: int):
    """Переставить участника на позицию (остальные сдвигаются). Начатые и завершённые не переставляются."""
    slot = StartSlot.objects.select_related("program").get(pk=slot_id)
    p = _lock_program(user, slot.program_id)
    slots = list(p.slots.order_by("position", "id"))
    slot = next(s for s in slots if s.pk == slot_id)
    if slot.locked:
        raise ValueError("Начатое или завершённое выступление нельзя переставить.")
    new_position = max(1, min(int(new_position), len(slots)))
    rest = [s for s in slots if s.pk != slot.pk]
    rest.insert(new_position - 1, slot)
    for i, s in enumerate(rest, start=1):
        if s.locked and s.position != i:
            raise ValueError("Нельзя сдвигать начатые и завершённые выступления — выберите другую позицию.")
    for i, s in enumerate(rest, start=1):
        s.position = i
    for s in rest:
        StartSlot.objects.filter(pk=s.pk).update(position=s.position)
    _recompute_times(p)
    audit(user, "startlist.move", p, slot=slot.pk, position=new_position)


@transaction.atomic
def swap(user, slot_a, slot_b):
    a = StartSlot.objects.get(pk=slot_a)
    p = _lock_program(user, a.program_id)
    a = StartSlot.objects.get(pk=slot_a)
    b = StartSlot.objects.get(pk=slot_b, program=p)
    if a.locked or b.locked:
        raise ValueError("Начатые и завершённые выступления не переставляются.")
    a.position, b.position = b.position, a.position
    a.save(update_fields=["position"])
    b.save(update_fields=["position"])
    _recompute_times(p)
    audit(user, "startlist.swap", p, a=a.pk, b=b.pk)


@transaction.atomic
def set_status(user, slot_id, status, reason=""):
    slot = StartSlot.objects.select_related("program").get(pk=slot_id)
    _lock_program(user, slot.program_id)
    slot.refresh_from_db()
    allowed = {
        StartSlot.Status.WAITING: {StartSlot.Status.STARTED, StartSlot.Status.WITHDRAWN, StartSlot.Status.NO_SHOW},
        StartSlot.Status.STARTED: {StartSlot.Status.FINISHED, StartSlot.Status.WITHDRAWN},
    }
    if status not in allowed.get(slot.status, set()):
        raise ValueError(f"Нельзя перевести из «{slot.get_status_display()}» в «{StartSlot.Status(status).label}».")
    slot.status = status
    slot.save(update_fields=["status", "updated_at"])
    if status in (StartSlot.Status.WITHDRAWN, StartSlot.Status.NO_SHOW) and not slot.program.is_manual:
        scoring.recompute(slot.program)
    elif status in (StartSlot.Status.WITHDRAWN, StartSlot.Status.NO_SHOW):
        r, _ = Result.objects.get_or_create(entry=slot.entry)
        r.status = Result.Status.WITHDRAWN if status == StartSlot.Status.WITHDRAWN else Result.Status.NO_SHOW
        r.updated_by = user
        r.save()
    audit(user, f"slot.{status}", slot.program, slot=slot.pk, reason=reason)


@transaction.atomic
def set_exception(user, slot_id, reason):
    slot = StartSlot.objects.get(pk=slot_id)
    _lock_program(user, slot.program_id)
    slot.exception_reason = (reason or "").strip()[:300]
    slot.save(update_fields=["exception_reason"])
    audit(user, "slot.exception", slot.program, slot=slot.pk, reason=slot.exception_reason)


def conflicts(tournament) -> dict[int, list[str]]:
    """Конфликты порядка: одна лошадь или один всадник стартуют, а между стартами меньше `min_gap_starts` других пар.
    {slot_id: [описания]}. Проверяется внутри программы; судья может оставить исключение с причиной."""
    gap = tournament.min_gap_starts
    out = {}
    if not gap:
        return out
    by_program = {}
    for sl in (StartSlot.objects.filter(program__tournament=tournament)
               .exclude(status__in=[StartSlot.Status.WITHDRAWN, StartSlot.Status.NO_SHOW])
               .select_related("entry", "program").order_by("program_id", "position")):
        by_program.setdefault(sl.program_id, []).append(sl)
    for slots in by_program.values():
        for i, a in enumerate(slots):
            for j in range(i + 1, min(len(slots), i + gap + 1)):
                b = slots[j]
                between = j - i - 1
                same = []
                if a.entry.horse_id == b.entry.horse_id:
                    same.append(f"лошадь {a.entry.horse_snapshot.get('name')}")
                if a.entry.athlete_id == b.entry.athlete_id:
                    same.append(f"всадник {a.entry.athlete_snapshot.get('name')}")
                if same:
                    text = f"{', '.join(same)}: №{a.position} и №{b.position} — между стартами {between} (нужно ≥ {gap})"
                    out.setdefault(a.pk, []).append(text)
                    out.setdefault(b.pk, []).append(text)
    return out


ATHLETE_PUBLIC = ("name", "birth_year", "rank", "club", "region", "coach")
HORSE_PUBLIC = ("name", "birth_year", "sex", "color", "breed")


def public_row(slot):
    e = slot.entry
    a = e.athlete_snapshot
    return {
        "entry": e.pk, "position": slot.position, "time": slot.start_time.strftime("%H:%M") if slot.start_time else "",
        "athlete": {k: a.get(k) for k in ATHLETE_PUBLIC},
        "horse": {k: e.horse_snapshot.get(k) for k in HORSE_PUBLIC},
        "class": e.class_name, "height": entry_height(e), "status": slot.status if slot.status in (StartSlot.Status.WITHDRAWN, StartSlot.Status.NO_SHOW) else "",
    }


@transaction.atomic
def publish_start_list(user, program_id) -> StartListPublication:
    p = _lock_program(user, program_id)
    slots = list(p.slots.select_related("entry").order_by("position"))
    if not slots:
        raise ValueError("Порядок пуст — сначала проведите жеребьёвку.")
    rows = [public_row(s) for s in slots]
    version = (p.start_publications.aggregate(m=Max("version"))["m"] or 0) + 1
    pub = StartListPublication.objects.create(program=p, version=version, rows=rows, published_by=user)
    audit(user, "startlist.publish", p, version=version)
    users = []
    for s in slots:
        users.append(s.entry.application.submitted_by)
        if s.entry.athlete.linked_user_id:
            users.append(s.entry.athlete.linked_user)
    notify(users, f"Опубликован стартовый протокол: «{p.tournament.pub_title}», {p.name} (версия {version})",
           reverse("tournaments:public", args=[p.tournament_id]) + "?tab=starts", "startlist")
    return pub


# ---------------- результаты ----------------

def parse_value(raw: str):
    raw = (raw or "").strip().replace(",", ".")
    if raw == "":
        return None
    try:
        v = float(raw)
    except ValueError:
        raise ValueError(f"«{raw}» — не число.")
    if v < 0:
        raise ValueError("Значение не может быть отрицательным.")
    return v


@transaction.atomic
def save_result(user, entry_id, values: dict, place, status, note="", finish=True):
    e = Entry.objects.select_for_update(of=("self",)).select_related("program", "tournament").get(pk=entry_id)
    require_judge(user, e.tournament)
    if not e.program.is_manual:
        raise ValueError("Результат этой программы считается автоматически — вводите данные на экране судьи.")
    clean = {}
    for f in e.program.result_fields:
        v = parse_value(values.get(f, ""))
        if v is not None:
            clean[f] = v
    try:
        place = int(place) if str(place).strip() else None
    except ValueError:
        raise ValueError("Место — целое число.")
    if place is not None and place < 1:
        raise ValueError("Место начинается с 1.")
    if status not in Result.Status.values:
        raise ValueError("Неизвестный статус.")
    r, _ = Result.objects.get_or_create(entry=e)
    r.values, r.place, r.status, r.note, r.updated_by = clean, place, status, note.strip()[:200], user
    r.save()
    slot = getattr(e, "start_slot", None)
    if slot and finish and slot.status in (StartSlot.Status.WAITING, StartSlot.Status.STARTED):
        slot.status = StartSlot.Status.FINISHED
        slot.save(update_fields=["status", "updated_at"])
    audit(user, "result.save", e, values=clean, place=place, status=status)
    return r


@transaction.atomic
def publish_results(user, class_id) -> ResultPublication:
    c = TournamentClass.objects.select_for_update().select_related("program__tournament").get(pk=class_id)
    p = c.program
    require_judge(user, p.tournament)
    if c.merged_into_id:
        raise ValueError(f"Зачёт объединён с «{c.merged_into.label}» — публикуйте результаты объединённого зачёта.")
    if p.is_dressage and not p.is_manual and not scoring.is_scorer(user, p) and not can_manage(user, p.tournament):
        raise ValueError("Результаты езды публикует судья-подсчёт.")
    classes = [c] + list(c.merged.all())
    entries = list(Entry.objects.filter(tclass__in=classes, decision=Entry.Decision.ACCEPTED).select_related("result", "tclass"))
    with_results = [e for e in entries if hasattr(e, "result") and not (e.result.values or {}).get("pending")]
    if not with_results:
        raise ValueError("Нет результатов для публикации.")
    if p.is_dressage and not p.is_manual:
        waiting = [e for e in with_results if not e.result.status and not (e.result.values or {}).get("approved")]
        if waiting:
            raise ValueError(f"Не утверждены результаты: {', '.join(e.athlete_snapshot.get('name', '') for e in waiting[:5])}.")
    rows = []
    for e in with_results:
        r = e.result
        rows.append({
            "entry": e.pk, "place": r.place,
            "athlete": {k: e.athlete_snapshot.get(k) for k in ATHLETE_PUBLIC},
            "horse": {k: e.horse_snapshot.get(k) for k in HORSE_PUBLIC},
            "class": e.tclass.label if e.tclass_id != c.pk else "",
            "cells": scoring.result_cells(p, r), "values": r.values if p.is_manual else {},
            "status": r.get_status_display() if r.status else "",
        })
    rows.sort(key=lambda x: (x["place"] is None, x["place"] or 0, x["athlete"]["name"] or ""))
    version = (c.result_publications.aggregate(m=Max("version"))["m"] or 0) + 1
    pub = ResultPublication.objects.create(tclass=c, version=version, rows=rows, fields=scoring.result_columns(p), published_by=user)
    audit(user, "results.publish", p.tournament, tclass=c.pk, version=version)
    users = [e.application.submitted_by for e in with_results] + [e.athlete.linked_user for e in with_results if e.athlete.linked_user_id]
    notify(users, f"Опубликованы результаты: «{p.tournament.pub_title}», {p.name} · {c.label}",
           reverse("tournaments:public", args=[p.tournament_id]) + "?tab=results", "results")
    return pub


def finish_warnings(tournament) -> list[str]:
    """Что не закрыто перед завершением турнира."""
    out = []
    for c in TournamentClass.objects.filter(program__tournament=tournament).select_related("program"):
        has = Entry.objects.filter(tclass=c, decision=Entry.Decision.ACCEPTED).exists()
        if has and not c.result_publications.exists():
            out.append(f"Не опубликованы результаты: {c.program.name} · {c.name}")
    waiting = StartSlot.objects.filter(program__tournament=tournament, status__in=[StartSlot.Status.WAITING, StartSlot.Status.STARTED]).count()
    if waiting:
        out.append(f"Выступлений без итогового статуса: {waiting}")
    for mod in ("apps.stables.services",):
        try:
            m = __import__(mod, fromlist=["finish_warnings"])
        except ImportError:
            continue
        out += m.finish_warnings(tournament)
    return out
