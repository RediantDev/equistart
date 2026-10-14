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

from . import services
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
        t.min_rest_minutes = max(0, min(600, int(request.POST.get("min_rest_minutes", "0"))))
    except ValueError:
        messages.error(request, "Интервал — целое число минут.")
        return redirect("conduct:overview", pk=t.pk)
    t.save(update_fields=["min_rest_minutes"])
    audit(request.user, "conduct.settings", t, min_rest_minutes=t.min_rest_minutes)
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
    for c in p.classes.all():
        last = c.result_publications.first()
        classes.append({"c": c, "published": last, "count": Entry.objects.filter(tclass=c, result__isnull=False).count()})
    return render(request, "conduct/program.html", {
        "t": t, "p": p, "slots": slots, "outside": outside, "classes": classes,
        "published": p.start_publications.first(),
        "fields": [(f, RESULT_FIELDS.get(f, f)) for f in p.result_fields], "statuses": Result.Status.choices,
    })


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
            p.slot_minutes = max(1, min(120, int(request.POST.get("slot_minutes", p.slot_minutes))))
            p.save(update_fields=["slot_minutes"])
            services._recompute_times(p)
            messages.success(request, "Интервал между стартами сохранён, время пересчитано.")
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
    rows = [["№", "Время", "Всадник", "Год рожд.", "Разряд", "Клуб", "Лошадь", "Год", "Пол", "Масть", "Порода", "Зачёт", "Отметка"]]
    for r in pub.rows:
        a, h = r["athlete"], r["horse"]
        rows.append([r["position"], r["time"], a["name"], a.get("birth_year") or "", a.get("rank") or "", a.get("club") or "",
                     h["name"], h.get("birth_year") or "", h.get("sex") or "", h.get("color") or "", h.get("breed") or "", r["class"], _mark(r)])
    return _xlsx_response(xlsx.build("Стартовый протокол", rows), f"start-{p.tournament_id}-{p.pk}-v{pub.version}.xlsx")


def results_xlsx(request, pk, class_id):
    c = get_object_or_404(TournamentClass.objects.select_related("program__tournament"), pk=class_id, program__tournament_id=pk,
                          program__tournament__publication__in=["published", "finished", "cancelled"])
    pub = c.result_publications.first()
    if not pub:
        raise Http404
    rows = [["Место", "Всадник", "Год рожд.", "Разряд", "Клуб", "Лошадь", "Год", "Пол", "Масть", "Порода"] + pub.fields + ["Статус"]]
    keys = c.program.result_fields
    for r in pub.rows:
        a, h = r["athlete"], r["horse"]
        rows.append([r["place"] or "", a["name"], a.get("birth_year") or "", a.get("rank") or "", a.get("club") or "", h["name"],
                     h.get("birth_year") or "", h.get("sex") or "", h.get("color") or "", h.get("breed") or ""]
                    + [r["values"].get(k) if r["values"].get(k) is not None else "" for k in keys] + [r["status"]])
    return _xlsx_response(xlsx.build("Результаты", rows), f"results-{pk}-{c.pk}-v{pub.version}.xlsx")


def _mark(row):
    return {"withdrawn": "Снят", "no_show": "Не явился"}.get(row.get("status"), "")
