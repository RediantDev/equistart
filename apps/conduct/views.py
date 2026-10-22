from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import xlsx
from apps.core.models import audit
from apps.entries.models import Entry
from apps.entries.services import require_judge
from apps.tournaments.disciplines import RESULT_FIELDS
from apps.tournaments.models import ProgramItem, Tournament, TournamentClass
from apps.tournaments.services import can_manage

from . import scoring, services
from .models import Result, ResultPublication, StartListPublication, StartSlot


def _program(request, pk, program_id):
    p = get_object_or_404(ProgramItem.objects.select_related("tournament"), pk=program_id, tournament_id=pk)
    require_judge(request.user, p.tournament)
    return p


@login_required
def overview(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    require_judge(request.user, t)
    programs = []
    for p in t.programs.prefetch_related("classes", "slots", "start_publications"):
        accepted = Entry.objects.filter(program=p, decision=Entry.Decision.ACCEPTED).count()
        ready = sum(1 for e in Entry.objects.filter(program=p) if services.eligible(e))
        last = p.start_publications.first()
        programs.append({"p": p, "accepted": accepted, "ready": ready, "slots": p.slots.count(), "published": last})
    return render(request, "conduct/overview.html", {
        "t": t, "programs": programs, "manage": can_manage(request.user, t),
        "warnings": services.finish_warnings(t) if t.publication == Tournament.Publication.PUBLISHED else [],
    })


@login_required
@require_POST
def settings_save(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    require_judge(request.user, t)
    try:
        t.min_gap_starts = max(0, min(100, int(request.POST.get("min_gap_starts", "0"))))
    except ValueError:
        messages.error(request, "Интервал — целое число стартов.")
        return redirect("conduct:overview", pk=t.pk)
    t.save(update_fields=["min_gap_starts"])
    audit(request.user, "conduct.settings", t, min_gap_starts=t.min_gap_starts)
    messages.success(request, "Минимальный интервал сохранён.")
    return redirect(request.POST.get("next") or reverse("conduct:overview", args=[t.pk]))


@login_required
@require_POST
def finish(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    if not can_manage(request.user, t):
        raise Http404
    if t.publication != Tournament.Publication.PUBLISHED:
        messages.error(request, "Завершить можно только опубликованный турнир.")
        return redirect("conduct:overview", pk=t.pk)
    if request.POST.get("confirm") != "1":
        messages.error(request, "Подтвердите завершение.")
        return redirect("conduct:overview", pk=t.pk)
    t.publication = Tournament.Publication.FINISHED
    t.finished_at = timezone.now()
    t.revision += 1
    t.save()
    audit(request.user, "tournament.finish", t, warnings=services.finish_warnings(t))
    messages.success(request, "Турнир завершён. Опубликованные сведения остаются в архиве; долги и возвраты можно закрывать и дальше.")
    return redirect("conduct:overview", pk=t.pk)


@login_required
def program(request, pk, program_id):
    p = _program(request, pk, program_id)
    t = p.tournament
    slots = list(p.slots.select_related("entry", "entry__result").order_by("position"))
    conf = services.conflicts(t)
    for s in slots:
        s.conflicts = conf.get(s.pk, [])
        s.result = getattr(s.entry, "result", None)
        s.still_eligible = services.eligible(s.entry)
    in_list = {s.entry_id for s in slots}
    outside = [(e, services.eligible(e)) for e in Entry.objects.filter(program=p).exclude(pk__in=in_list).exclude(decision__in=[Entry.Decision.CANCELLED, Entry.Decision.REJECTED])]
    classes = []
    for c in p.classes.filter(merged_into__isnull=True):
        last = c.result_publications.first()
        classes.append({"c": c, "published": last, "count": Entry.objects.filter(tclass__in=[c, *c.merged.all()], result__isnull=False).count()})
    ctx = {
        "t": t, "p": p, "slots": slots, "outside": outside, "classes": classes,
        "published": p.start_publications.first(),
        "fields": [(f, RESULT_FIELDS.get(f, f)) for f in p.result_fields], "statuses": Result.Status.choices,
        "auto": not p.is_manual,
    }
    if not p.is_manual:
        for s in slots:
            s.cells = scoring.result_cells(p, s.result) if s.result else []
        ctx["columns"] = scoring.result_columns(p)
        ctx["standings"] = _standings_tables(p)
    if p.is_jumping and not p.route.manual:
        from apps.sport import jumping as J

        c = scoring.course(p)
        ctx["course"] = c
        ctx["course_rounds"] = [{"spec": r, "data": c["rounds"].get(r.key, {}), "params": scoring.round_params(p, r.key)}
                                for r in J.rounds_of(p.route, p.variant)]
    if p.is_dressage and p.test_snapshot:
        ctx["has_panel"], ctx["panel"] = True, scoring.panel(p)
        ctx["my_letters"] = [x.letter for x in ctx["panel"] if x.user_id == request.user.pk and not x.is_scorer]
        ctx["is_scorer"] = scoring.is_scorer(request.user, p)
    return render(request, "conduct/program.html", ctx)


def _standings_tables(p):
    """Текущие места по классификациям (зачёт + высота) — для судьи, до публикации."""
    groups = {}
    for e in Entry.objects.filter(program=p, decision=Entry.Decision.ACCEPTED, result__isnull=False).select_related("result", "tclass"):
        key = scoring.group_key(e)
        groups.setdefault(key, []).append(e)
    out = []
    for (cid, height), items in groups.items():
        c = TournamentClass.objects.get(pk=cid)
        items.sort(key=lambda e: (e.result.place is None, e.result.place or 0, e.athlete_snapshot.get("name", "")))
        out.append({"title": c.name + (f" · {height} см" if height and len(p.heights) > 1 else ""),
                    "rows": [{"e": e, "r": e.result, "cells": scoring.result_cells(p, e.result)} for e in items]})
    return out


@login_required
@require_POST
def program_action(request, pk, program_id, action):
    p = _program(request, pk, program_id)
    try:
        if action == "draw":
            n = services.draw(request.user, p.pk)
            messages.success(request, f"Жеребьёвка проведена. Добавлено новых: {n}. Начатые остались на местах.")
        elif action == "add":
            n = services.add_missing(request.user, p.pk)
            messages.success(request, f"Добавлено в конец порядка: {n}.")
        elif action == "move":
            services.move(request.user, int(request.POST["slot"]), int(request.POST["position"]))
            messages.success(request, "Порядок изменён, время пересчитано.")
        elif action == "swap":
            services.swap(request.user, int(request.POST["slot"]), int(request.POST["other"]))
            messages.success(request, "Участники поменялись местами.")
        elif action == "status":
            services.set_status(request.user, int(request.POST["slot"]), request.POST["status"])
            messages.success(request, "Статус обновлён.")
        elif action == "exception":
            services.set_exception(request.user, int(request.POST["slot"]), request.POST.get("reason", ""))
            messages.success(request, "Исключение сохранено.")
        elif action == "timing":
            if not p.is_dressage:
                raise ValueError("В конкуре время стартов не рассчитывается — указывается только начало маршрута.")
            p.slot_minutes = max(1, min(120, int(request.POST.get("slot_minutes", p.slot_minutes))))
            p.start_times = request.POST.get("start_times") == "1"
            p.save(update_fields=["slot_minutes", "start_times"])
            services._recompute_times(p)
            messages.success(request, "Время стартов пересчитано." if p.timed_starts else "Время стартов не рассчитывается — в протоколе будет только порядок.")
        elif action == "publish":
            pub = services.publish_start_list(request.user, p.pk)
            messages.success(request, f"Стартовый протокол опубликован (версия {pub.version}).")
        elif action == "result":
            values = {f: request.POST.get(f"v_{f}", "") for f in p.result_fields}
            services.save_result(request.user, int(request.POST["entry"]), values, request.POST.get("place", ""), request.POST.get("status", ""), request.POST.get("note", ""))
            messages.success(request, "Результат сохранён (не опубликован).")
        elif action == "publish_results":
            c = get_object_or_404(TournamentClass, pk=request.POST.get("tclass"), program=p)
            pub = services.publish_results(request.user, c.pk)
            messages.success(request, f"Результаты зачёта «{c.name}» опубликованы (версия {pub.version}).")
        else:
            raise Http404
    except (ValueError, KeyError, StartSlot.DoesNotExist) as e:
        messages.error(request, str(e) if str(e) else "Проверьте данные.")
    nxt = request.POST.get("next")
    return redirect(nxt if nxt and nxt.startswith("/") else reverse("conduct:program", args=[pk, p.pk]))


@login_required
def live(request, pk, program_id):
    """Экран судьи у поля: текущий участник, следующие, быстрый ввод результата."""
    p = _program(request, pk, program_id)
    if p.is_jumping and not p.route.manual:
        return redirect("conduct:jump_live", pk=pk, program_id=p.pk)
    if p.is_dressage and p.test_snapshot:
        mine = [x for x in scoring.panel(p) if x.user_id == request.user.pk]
        if any(x.is_scorer for x in mine):
            return redirect("conduct:scorer", pk=pk, program_id=p.pk)
        if mine:
            return redirect("conduct:judge_sheet", pk=pk, program_id=p.pk, letter=mine[0].letter)
        return redirect(reverse("conduct:program", args=[pk, p.pk]) + "#panel")
    slots = list(p.slots.select_related("entry", "entry__result").order_by("position"))
    current = next((s for s in slots if s.status == StartSlot.Status.STARTED), None) or next((s for s in slots if s.status == StartSlot.Status.WAITING), None)
    upcoming = [s for s in slots if s.status == StartSlot.Status.WAITING and s != current][:4]
    done = sum(1 for s in slots if s.status not in (StartSlot.Status.WAITING, StartSlot.Status.STARTED))
    conf = services.conflicts(p.tournament)
    return render(request, "conduct/live.html", {
        "t": p.tournament, "p": p, "current": current, "upcoming": upcoming, "done": done, "total": len(slots),
        "fields": [(f, RESULT_FIELDS.get(f, f)) for f in p.result_fields], "statuses": Result.Status.choices,
        "current_result": getattr(current.entry, "result", None) if current else None,
        "current_conflicts": conf.get(current.pk, []) if current else [],
    })


@login_required
@require_POST
def live_save(request, pk, program_id):
    p = _program(request, pk, program_id)
    slot = get_object_or_404(StartSlot, pk=request.POST.get("slot"), program=p)
    try:
        values = {f: request.POST.get(f"v_{f}", "") for f in p.result_fields}
        services.save_result(request.user, slot.entry_id, values, request.POST.get("place", ""), request.POST.get("status", ""))
        nxt = p.slots.filter(status=StartSlot.Status.WAITING).order_by("position").first()
        if nxt and request.POST.get("next_start") == "1":
            services.set_status(request.user, nxt.pk, StartSlot.Status.STARTED)
        messages.success(request, f"Сохранено: {slot.entry.athlete_snapshot.get('name')}.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect("conduct:live", pk=pk, program_id=p.pk)


# ---------------- выгрузки ----------------

def _xlsx_response(data: bytes, name: str):
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


def startlist_xlsx(request, pk, program_id):
    p = get_object_or_404(ProgramItem.objects.select_related("tournament"), pk=program_id, tournament_id=pk,
                          tournament__publication__in=["published", "finished", "cancelled"])
    pub = p.start_publications.first()
    if not pub:
        raise Http404
    timed = any(r.get("time") for r in pub.rows)
    rows = [["№"] + (["Время"] if timed else []) + ["Всадник", "Год рожд.", "Разряд", "Клуб", "Тренер", "Лошадь", "Год", "Пол", "Масть", "Порода", "Зачёт", "Высота", "Отметка"]]
    for r in pub.rows:
        a, h = r["athlete"], r["horse"]
        rows.append([r["position"]] + ([r["time"]] if timed else []) + [a["name"], a.get("birth_year") or "", a.get("rank") or "", a.get("club") or "", a.get("coach") or "",
                     h["name"], h.get("birth_year") or "", h.get("sex") or "", h.get("color") or "", h.get("breed") or "", r["class"],
                     r.get("height") or "", _mark(r)])
    return _xlsx_response(xlsx.build("Стартовый протокол", rows), f"start-{p.tournament_id}-{p.pk}-v{pub.version}.xlsx")


def results_xlsx(request, pk, class_id):
    c = get_object_or_404(TournamentClass.objects.select_related("program__tournament"), pk=class_id, program__tournament_id=pk,
                          program__tournament__publication__in=["published", "finished", "cancelled"])
    pub = c.result_publications.first()
    if not pub:
        raise Http404
    multi = len(c.program.heights or []) > 1
    rows = [(["Высота, см"] if multi else []) + ["Место", "Всадник", "Год рожд.", "Разряд", "Клуб", "Тренер", "Лошадь", "Год", "Пол", "Масть", "Порода"] + pub.fields + ["Статус"]]
    keys = c.program.result_fields
    hmap = {}
    if multi and any("height" not in r for r in pub.rows):
        hmap = dict(Entry.objects.filter(pk__in=[r["entry"] for r in pub.rows]).values_list("pk", "height"))
    ordered = sorted(pub.rows, key=lambda r: ((r.get("height") or hmap.get(r["entry"]) or 0) if multi else 0, r["place"] is None, r["place"] or 0))
    for r in ordered:
        a, h = r["athlete"], r["horse"]
        cells = r["cells"] if "cells" in r else [r["values"].get(k) for k in keys]
        rows.append(([r.get("height") or hmap.get(r["entry"]) or ""] if multi else []) + [r["place"] or "", a["name"], a.get("birth_year") or "", a.get("rank") or "", a.get("club") or "", a.get("coach") or "",
                     h["name"], h.get("birth_year") or "", h.get("sex") or "", h.get("color") or "", h.get("breed") or ""]
                    + ["" if v is None else v for v in cells] + [r["status"]])
    return _xlsx_response(xlsx.build("Результаты", rows), f"results-{pk}-{c.pk}-v{pub.version}.xlsx")


def _mark(row):
    return {"withdrawn": "Снят", "no_show": "Не явился"}.get(row.get("status"), "")
