"""Экраны судейства: конкур у поля (JMP-03…05, SCR-02) и выездка — судьи на буквах, судья на C, судья-подсчёт (DRS-02…10)."""

import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.entries.models import Entry
from apps.entries.services import can_judge, require_judge
from apps.sport import dressage as D
from apps.sport import jumping as J
from apps.tournaments.models import ProgramItem

from . import scoring
from .models import DressageFlags, DressageSheet, JudgePosition, JumpRound, Result

QUICK_COMMENTS = ["не прямо", "потеря ритма", "неточный переход", "напряжение", "сопротивление", "нет сгибания",
                  "потеря контакта", "не по букве", "мало импульса", "хорошо"]

EXCLUSION_REASONS = [
    "второе неповиновение", "третье неповиновение", "падение всадника или лошади", "превышен лимит времени",
    "ошибка в схеме, не исправлена", "не взят старт в течение 45 с", "прочее (указать)",
]


def _fetch(request):
    return request.headers.get("X-Requested-With") == "fetch"


def _program(request, pk, program_id):
    p = get_object_or_404(ProgramItem.objects.select_related("tournament"), pk=program_id, tournament_id=pk)
    return p


def _judge_program(request, pk, program_id):
    p = _program(request, pk, program_id)
    require_judge(request.user, p.tournament)
    return p


# ======================= КОНКУР =======================

@login_required
@require_POST
def course_save(request, pk, program_id):
    p = _judge_program(request, pk, program_id)
    try:
        scoring.save_course(request.user, p, request.POST)
        messages.success(request, "Параметры маршрута сохранены. Результаты пересчитаны.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect(reverse("conduct:program", args=[pk, p.pk]) + "#course")


def _stage_list(p):
    out = []
    for spec in J.rounds_of(p.route, p.variant):
        rp = scoring.round_params(p, spec.key)
        out.append({"key": spec.key, "title": spec.title, "timed": J.round_timed(p.route, p.variant, spec.key), "table": rp["table"],
                    "time_allowed": rp["time_allowed"], "limit": rp["limit"]})
    return out


@login_required
def jump_live(request, pk, program_id):
    """Экран судьи у поля для маршрута с автоматическим подсчётом."""
    p = _judge_program(request, pk, program_id)
    if not p.is_jumping or p.route.manual:
        return redirect("conduct:live", pk=pk, program_id=p.pk)
    stages = _stage_list(p)
    keys = [s["key"] for s in stages]
    stage = request.GET.get("stage") if request.GET.get("stage") in keys else keys[0]
    st = next(s for s in stages if s["key"] == stage)
    queue = scoring.jump_queue(p, stage)
    current = None
    want = request.GET.get("entry")
    if want:
        current = next(((s, r) for s, r in queue if str(s.entry_id) == want), None)
    if current is None:
        current = next(((s, r) for s, r in queue if r is None), None)
    counts = {k: len([1 for _, r in scoring.jump_queue(p, k) if r is not None]) for k in keys}
    places = {}
    for items in scoring.standings(p).values():
        for x in items:
            places[x.key.pk] = x
    last = None
    if request.GET.get("saved"):
        last = places.get(int(request.GET["saved"])) if request.GET["saved"].isdigit() else None
    return render(request, "conduct/jump_live.html", {
        "t": p.tournament, "p": p, "stages": stages, "stage": st, "queue": queue, "current": current,
        "counts": counts, "places": places, "last": last, "reasons": EXCLUSION_REASONS,
        "client_key": uuid.uuid4().hex, "course": scoring.course(p),
        "with_corrections": st["table"] == "C", "next_stage": _next_stage(p, stage),
    })


def _next_stage(p, stage):
    keys = [r.key for r in J.rounds_of(p.route, p.variant)]
    i = keys.index(stage)
    return keys[i + 1] if i + 1 < len(keys) else None


@login_required
@require_POST
def jump_save(request, pk, program_id):
    p = _judge_program(request, pk, program_id)
    stage = request.POST.get("stage", "")
    entry_id = request.POST.get("entry", "")
    try:
        entry_id = int(entry_id)
        if not Entry.objects.filter(pk=entry_id, program=p).exists():
            raise Http404
        data = dict(request.POST.items())
        jr = scoring.save_round(request.user, entry_id, stage, data, request.POST.get("client_key", ""))
    except ValueError as e:
        if _fetch(request):
            return JsonResponse({"ok": False, "error": str(e)}, status=422)
        messages.error(request, str(e))
        return redirect(reverse("conduct:jump_live", args=[pk, p.pk]) + f"?stage={stage}&entry={entry_id}")
    # «В две фазы»: прошедший 1-ю фазу сразу продолжает 2-ю
    url = reverse("conduct:jump_live", args=[pk, p.pk])
    nxt = _next_stage(p, stage)
    go = f"?stage={stage}&saved={entry_id}"
    if nxt and p.route.key in ("two_phases", "two_phases_special") and stage == "phase1":
        if not JumpRound.objects.filter(entry_id=entry_id, round=nxt).exists() and jr.entry in scoring.next_stage_candidates(p, nxt):
            go = f"?stage={nxt}&entry={entry_id}&saved={entry_id}"
    res = getattr(Entry.objects.get(pk=entry_id), "result", None)
    text = f"Сохранено: {jr.entry.athlete_snapshot.get('name')}" + (f" — {res.place} место" if res and res.place else "")
    if _fetch(request):
        return JsonResponse({"ok": True, "message": text, "next": url + go})
    messages.success(request, text)
    return redirect(url + go)


# ======================= ВЫЕЗДКА =======================

@login_required
def panel(request, pk, program_id):
    p = _judge_program(request, pk, program_id)
    if not p.is_dressage or not p.test_snapshot:
        raise Http404
    current = {x.letter: x for x in scoring.panel(p)}
    if request.method == "POST":
        rows = []
        for i in range(6):
            rows.append({k: request.POST.get(f"{k}_{i}", "") for k in ("letter", "role", "user", "judge_name", "judge_category", "judge_region")})
        try:
            scoring.save_panel(request.user, p, rows)
            messages.success(request, "Бригада сохранена. Каждый судья увидит свой лист в разделе «Проведение».")
            return redirect(reverse("conduct:program", args=[pk, p.pk]) + "#panel")
        except ValueError as e:
            messages.error(request, str(e))
            posted = rows
    else:
        posted = None
    rows = []
    defaults = ["C", "E", "B", "SCORER", "", ""]
    for i in range(6):
        if posted:
            rows.append(posted[i])
            continue
        existing = list(current.values())
        if existing:
            x = existing[i] if i < len(existing) else None
            rows.append({"letter": x.letter, "role": x.role, "user": str(x.user_id), "judge_name": x.judge_name,
                         "judge_category": x.judge_category, "judge_region": x.judge_region} if x else {"letter": ""})
        else:
            rows.append({"letter": ""})  # Q6: буквы выбирает судья, значения по умолчанию нет
    return render(request, "conduct/panel.html", {
        "t": p.tournament, "p": p, "rows": rows, "letters": JudgePosition.Letter.choices, "candidates": scoring.panel_candidates(p),
        "children": p.test_snapshot.get("kind") == "children", "roles": JudgePosition.Role.choices, "hint": defaults,
    })


def _sheet_rows(test, position, sheet):
    rows = []
    for part in test["parts"]:
        if position.role not in ("all", part["key"]):
            continue
        marks = (sheet.marks or {}).get(part["key"]) or []
        items = D.items_of(part)
        n_mov = len(part.get("movements", []))
        for i, it in enumerate(items):
            rows.append({"part": part["key"], "part_title": part["title"], "i": i, "n": it.get("n", ""), "letters": it.get("letters", ""),
                         "text": it.get("text", ""), "coef": it.get("coef", 1), "collective": i >= n_mov,
                         "value": marks[i] if i < len(marks) else None,
                         "comment": (sheet.comments or {}).get(f"{part['key']}:{i}", ""), "step": str(part.get("step", "0.5"))})
    return rows


@login_required
def judge_sheet(request, pk, program_id, letter):
    """Лист судьи на букве (DRS-03/04): только свои оценки, чужие не видны до подписи."""
    p = _program(request, pk, program_id)
    pos = scoring.my_position(request.user, p, letter)
    if not pos:
        raise PermissionDenied
    test = scoring.test_of(p)
    queue = scoring.dressage_queue(p)
    if not queue:
        return render(request, "conduct/judge_sheet.html", {"t": p.tournament, "p": p, "pos": pos, "queue": []})
    want = request.GET.get("entry")
    sheets = {s.entry_id: s for s in DressageSheet.objects.filter(position=pos)}
    entry = next((e for e, _ in queue if str(e.pk) == want), None)
    if entry is None:
        entry = next((e for e, _ in queue if not (sheets.get(e.pk) and sheets[e.pk].signed)), queue[0][0])
    sheet = sheets.get(entry.pk) or scoring.sheet_for(entry, pos)
    flags = DressageFlags.objects.filter(entry=entry).first()
    rows = _sheet_rows(test, pos, sheet)
    js = None
    if sheet.signed:
        try:
            js = D.judge_score(test, pos.letter, pos.role, {k: [Decimal(x) for x in v] for k, v in sheet.marks.items()},
                               flags.errors if flags else 0, flags.technical if flags else 0)
        except Exception:
            js = None
    return render(request, "conduct/judge_sheet.html", {
        "t": p.tournament, "p": p, "pos": pos, "test": test, "queue": [(e, s, sheets.get(e.pk)) for e, s in queue], "entry": entry,
        "sheet": sheet, "rows": rows, "flags": flags, "is_c": pos.letter == "C", "score": js, "reasons": EXCLUSION_REASONS,
        "missing": scoring.sheet_missing(test, pos, sheet), "scheme": D.PENALTY_SCHEMES.get(test.get("penalty_scheme") or "", {}).get("label", ""),
        "quick": QUICK_COMMENTS,
    })


@login_required
@require_POST
def judge_mark(request, pk, program_id, letter):
    p = _program(request, pk, program_id)
    entry_id = request.POST.get("entry")
    try:
        sh = scoring.save_mark(request.user, int(entry_id), letter, request.POST.get("part", ""), int(request.POST.get("i", "-1")),
                               request.POST.get("value", ""), request.POST.get("comment"))
        ok, err = True, ""
    except (ValueError, TypeError) as e:
        ok, err = False, str(e)
    if _fetch(request):
        return JsonResponse({"ok": ok, "error": err}, status=200 if ok else 422)
    if not ok:
        messages.error(request, err)
    return redirect(reverse("conduct:judge_sheet", args=[pk, p.pk, letter]) + f"?entry={entry_id}")


@login_required
@require_POST
def judge_sign(request, pk, program_id, letter):
    p = _program(request, pk, program_id)
    entry_id = request.POST.get("entry")
    try:
        # сохранить все оценки формы одним действием (на случай, если поля не отправились по одной)
        for key, val in request.POST.items():
            if key.startswith("m_"):
                _, part, i = key.split("_", 2)
                scoring.save_mark(request.user, int(entry_id), letter, part, int(i), val, request.POST.get(f"c_{part}_{i}"))
        scoring.sign(request.user, int(entry_id), letter, request.POST.get("remark", ""))
        messages.success(request, "Лист подписан и передан судье-подсчёту.")
        return redirect("conduct:judge_sheet", pk=pk, program_id=p.pk, letter=letter)
    except (ValueError, TypeError) as e:
        messages.error(request, str(e))
    return redirect(reverse("conduct:judge_sheet", args=[pk, p.pk, letter]) + f"?entry={entry_id}")


@login_required
@require_POST
def flags_save(request, pk, program_id):
    p = _program(request, pk, program_id)
    entry_id = request.POST.get("entry")
    back = request.POST.get("next") or reverse("conduct:judge_sheet", args=[pk, p.pk, "C"])
    try:
        elim = request.POST.get("eliminated") == "1"
        reason = request.POST.get("reason", "")
        if reason == "прочее (указать)":
            reason = request.POST.get("reason_other", "") or reason
        scoring.set_flags(request.user, int(entry_id), errors=request.POST.get("errors", 0) or 0,
                          technical=request.POST.get("technical", 0) or 0, eliminated=elim, reason=reason)
        messages.success(request, "Отметки сохранены.")
    except (ValueError, TypeError) as e:
        messages.error(request, str(e))
    return redirect(back if back.startswith("/") else "/")


@login_required
def scorer(request, pk, program_id):
    """Судья-подсчёт (DRS-09): статусы листов, проценты по судьям, расхождения, возврат, утверждение, публикация."""
    p = _program(request, pk, program_id)
    if not scoring.is_scorer(request.user, p) and not (can_judge(request.user, p.tournament) and request.user.pk == p.tournament.owner_id):
        raise PermissionDenied
    test = scoring.test_of(p)
    poss = scoring.letters(p)
    scores = scoring.pair_scores(p)
    order = {e.pk: (s.position if s else None) for e, s in scoring.dressage_queue(p)}
    rows = []
    for e, (res, sheets, fl) in sorted(scores.items(), key=lambda x: (order.get(x[0].pk) or 10_000, x[0].pk)):
        cells = []
        pcts = []
        for pos in poss:
            sh = sheets.get(pos.letter)
            js = next((j for j in (res.judges if res else []) if j.position == pos.letter), None)
            if js:
                pcts.append(js.pct)
            cells.append({"letter": pos.letter, "signed": bool(sh and sh.signed), "started": bool(sh and any(v for row in (sh.marks or {}).values() for v in row)),
                          "returned": sh.returned_reason if sh else "", "pct": js.pct if js else None})
        # DRS-07: подсвечиваем судью, чей процент отличается от среднего остальных на 5 пунктов и более
        off = []
        if len(pcts) > 1:
            for c in cells:
                if c["pct"] is None:
                    continue
                others = [cc["pct"] for cc in cells if cc is not c and cc["pct"] is not None]
                if others:
                    diff = abs(c["pct"] - sum(others) / len(others))
                    c["diff"] = diff
                    if diff >= 5:
                        c["off"] = True
                        off.append((c["letter"], diff))
        spread = max((d for _, d in off), default=None)
        result = getattr(e, "result", None)
        rows.append({"e": e, "res": res, "cells": cells, "flags": fl, "spread": spread, "off": off, "warn": bool(off),
                     "place": result.place if result else None, "approved": bool(fl and fl.approved_at)})
    classes = []
    for c in p.classes.filter(merged_into__isnull=True):
        classes.append({"c": c, "published": c.result_publications.first(),
                        "ready": Entry.objects.filter(tclass__in=[c, *c.merged.all()], result__isnull=False).exists()})
    return render(request, "conduct/scorer.html", {"t": p.tournament, "p": p, "test": test, "poss": poss, "rows": rows, "classes": classes})


@login_required
@require_POST
def scorer_action(request, pk, program_id, action):
    p = _program(request, pk, program_id)
    entry_id = request.POST.get("entry")
    try:
        if action == "return":
            scoring.return_sheet(request.user, int(entry_id), request.POST.get("letter", ""), request.POST.get("reason", ""))
            messages.success(request, "Лист возвращён судье.")
        elif action == "approve":
            scoring.approve(request.user, int(entry_id))
            messages.success(request, "Результат утверждён.")
        elif action == "approve_all":
            n = 0
            for e, (res, _, fl) in scoring.pair_scores(p).items():
                if res is not None and not (fl and fl.approved_at):
                    scoring.approve(request.user, e.pk)
                    n += 1
            messages.success(request, f"Утверждено: {n}.")
        elif action == "norm":
            scoring.set_norm(request.user, int(entry_id), request.POST.get("norm", ""))
            messages.success(request, "Норматив сохранён.")
        else:
            raise Http404
    except (ValueError, TypeError, DressageSheet.DoesNotExist) as e:
        messages.error(request, str(e) or "Проверьте данные.")
    return redirect(reverse("conduct:scorer", args=[pk, p.pk]) + (f"#e{entry_id}" if entry_id else ""))


@login_required
def my_sheets(request, entry_id):
    """Листы судей для участника (ACL-05): после публикации результатов зачёта."""
    from apps.entries.services import can_view_application

    e = get_object_or_404(Entry.objects.select_related("program__tournament", "application", "tclass"), pk=entry_id)
    if not (can_view_application(request.user, e.application) or (e.athlete.linked_user_id == request.user.pk)):
        raise Http404
    p = e.program
    if not p.is_dressage or not p.test_snapshot:
        raise Http404
    published = e.tclass.result_publications.exists() or (e.tclass.merged_into_id and e.tclass.merged_into.result_publications.exists())
    if not published and not can_judge(request.user, p.tournament):
        raise Http404
    test = p.test_snapshot
    sheets = []
    for sh in DressageSheet.objects.filter(entry=e, signed_at__isnull=False).select_related("position").order_by("position_id"):
        sheets.append({"pos": sh.position, "rows": _sheet_rows(test, sh.position, sh), "remark": sh.remark})
    return render(request, "conduct/my_sheets.html", {"e": e, "p": p, "t": p.tournament, "test": test, "sheets": sheets,
                                                      "result": getattr(e, "result", None), "flags": getattr(e, "dressage_flags", None)})
