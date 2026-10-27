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
from django.db.models import Count

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
    from django.db.models import Count

    blocked = services.blocked_for(t.pk)
    accepted_by = {}
    for pid, horse_id in Entry.objects.filter(tournament=t, decision=Entry.Decision.ACCEPTED).values_list("program_id", "horse_id"):
        a, r = accepted_by.get(pid, (0, 0))
        accepted_by[pid] = (a + 1, r + (horse_id not in blocked))
    slots_by = dict(StartSlot.objects.filter(program__tournament=t).values("program_id").annotate(n=Count("id")).values_list("program_id", "n"))
    last_pub = {}
    for pub in StartListPublication.objects.filter(program__tournament=t).order_by("program_id", "-version"):
        last_pub.setdefault(pub.program_id, pub)
    programs = []
    for p in t.programs.all().order_by("day", "start_time", "position", "id"):
        accepted, ready = accepted_by.get(p.pk, (0, 0))
        programs.append({"p": p, "accepted": accepted, "ready": ready, "slots": slots_by.get(p.pk, 0), "published": last_pub.get(p.pk)})
    return render(request, "conduct/overview.html", {
        "t": t, "programs": programs, "manage": can_manage(request.user, t),
        "warnings": _explain_warnings(t, services.finish_warnings(t)) if t.publication == Tournament.Publication.PUBLISHED else [],
    })


def _explain_warnings(t, items):
    """Тест 4, п. 124: у каждого незакрытого пункта — что это значит и куда перейти, чтобы исправить."""
    stables = reverse("stables:manage", args=[t.pk])
    out = []
    for w in items:
        if w.startswith("Брони без решения"):
            out.append((w, "Подтвердите или отклоните брони денников.", stables, "Денники и услуги"))
        elif w.startswith("Подтверждённые брони без номера"):
            out.append((w, "Укажите номер денника в подтверждённых бронях.", stables, "Денники и услуги"))
        elif w.startswith("Заказы услуг"):
            out.append((w, "Отметьте выдачу опилок, сена и других услуг.", stables, "Денники и услуги"))
        elif w.startswith("Не опубликованы результаты"):
            name = w.split(": ", 1)[-1].rsplit(" · ", 1)[0]
            prog = t.programs.filter(name=name).first()
            out.append((w, "Опубликуйте результаты зачёта.", reverse("conduct:program", args=[t.pk, prog.pk]) + "?tab=results" if prog else "", "Результаты"))
        elif w.startswith("Выступлений без итогового статуса"):
            out.append((w, "Есть пары, которые не выступили и не отмечены «Не стартует». Откройте порядок маршрута и отметьте их.", "", ""))
        else:
            out.append((w, "", "", ""))
    return out


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
    messages.success(request, "Турнир завершён. Опубликованные протоколы и результаты остаются в архиве и доступны всем.")
    return redirect("conduct:overview", pk=t.pk)


TABS = [("order", "Порядок"), ("entries", "Заявки"), ("protocol", "Стартовый протокол"), ("course", "Параметры маршрута"),
        ("panel", "Судейская бригада"), ("results", "Результаты"), ("merge", "Объединение зачётов")]


@login_required
def program(request, pk, program_id):
    p = _program(request, pk, program_id)
    t = p.tournament
    tab = request.GET.get("tab", "order")
    # Тест 4: принятые после жеребьёвки сами встают в конец порядка — без кнопки «Добавить готовых в конец»
    if p.slots.exists() and request.method == "GET":
        before = set(p.slots.values_list("entry_id", flat=True))
        if services.add_missing(request.user, p.pk):
            names = [e.athlete_snapshot.get("name") for e in Entry.objects.filter(start_slot__program=p).exclude(pk__in=before)]
            messages.info(request, "В конец порядка добавлены принятые после жеребьёвки: " + ", ".join(names) + ".")
    slots = list(p.slots.select_related("entry", "entry__result").order_by("position"))
    conf = services.conflicts(t, p)
    blocked = services.blocked_for(t.pk)
    for s in slots:
        s.conflicts = conf.get(s.pk, [])
        s.result = getattr(s.entry, "result", None)
        s.still_eligible = services.eligible(s.entry, blocked)
    in_list = {s.entry_id for s in slots}
    outside = [(e, services.eligible(e, blocked)) for e in Entry.objects.filter(program=p).exclude(pk__in=in_list).exclude(decision__in=[Entry.Decision.CANCELLED, Entry.Decision.REJECTED])]
    classes = []
    all_classes = list(p.classes.all())
    merged = [c for c in all_classes if c.merged_into_id]
    for c in p.classes.filter(merged_into__isnull=True):
        last = c.result_publications.first()
        real = [e for e in Entry.objects.filter(tclass__in=[c, *c.merged.all()], decision=Entry.Decision.ACCEPTED, result__isnull=False).select_related("result")
                if services.has_real_result(e.result)]
        classes.append({"c": c, "published": last, "count": len(real)})
    tabs = [x for x in TABS if not (x[0] == "course" and not (p.is_jumping and not p.route.manual))
            and not (x[0] == "panel" and not (p.is_dressage and p.test_snapshot)) and not (x[0] == "merge" and len(all_classes) < 2)]
    if tab not in dict(tabs):
        tab = "order"
    if tab == "entries":
        ents = list(Entry.objects.filter(program=p).select_related("application", "tclass").order_by("decision", "tclass__position", "athlete_snapshot__name"))
        order = {"pending": 0, "accepted": 1, "rejected": 2, "cancelled": 3}
        ents.sort(key=lambda e: order.get(e.decision, 9))
        ctx_entries = ents
    else:
        ctx_entries = []
    ctx = {
        "program_entries": ctx_entries, "blocked_ids": blocked,
        "tab": tab, "tabs": tabs, "others": t.programs.exclude(pk=p.pk).order_by("day", "start_time", "position"),
        "t": t, "p": p, "slots": slots, "outside": outside, "classes": classes,
        "published": p.start_publications.first(),
        "fields": [(f, RESULT_FIELDS.get(f, f)) for f in p.result_fields], "statuses": Result.Status.choices,
        "auto": not p.is_manual,
        "merged": merged, "all_classes": all_classes,
        "entries_by_class": {cid: n for cid, n in Entry.objects.filter(program=p).exclude(decision__in=[Entry.Decision.CANCELLED, Entry.Decision.REJECTED]).values_list("tclass_id").annotate(n=Count("id"))},
        "can_publish": not (p.is_dressage and not p.is_manual) or scoring.is_scorer(request.user, p) or can_manage(request.user, t),
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
        ctx["has_table_c"] = any(r["params"]["table"] == "C" for r in ctx["course_rounds"])
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
        out.append({"title": c.name + (f" · {height} см" if height and len(p.heights) > 1 else ""), "published": c.result_publications.exists(),
                    "rows": [{"e": e, "r": e.result, "cells": scoring.result_cells(p, e.result)} for e in items]})
    return out


@login_required
@require_POST
def program_action(request, pk, program_id, action):
    p = _program(request, pk, program_id)
    try:
        if action == "draw":
            r = services.draw(request.user, p.pk)
            if r["shuffled"] >= 2:
                messages.success(request, f"Жеребьёвка проведена: порядок {r['shuffled']} пар перемешан случайно"
                                          + (f", новых добавлено: {r['added']}" if r["added"] else "") + ". Выступившие и не стартующие остались на местах.")
            elif r["added"]:
                messages.success(request, f"В порядок добавлено пар: {r['added']}.")
            else:
                messages.info(request, "Перемешивать нечего: в порядке меньше двух пар, которые ещё не выступили.")
        elif action == "participation":
            sl = services.set_participation(request.user, int(request.POST["slot"]), request.POST.get("value") == "start")
            messages.success(request, f"{sl.entry.athlete_snapshot.get('name')}: {'стартует' if request.POST.get('value') == 'start' else 'не стартует'}.")
        elif action == "force":
            n = services.force_add(request.user, p.pk, int(request.POST["entry"]))
            messages.success(request, "Пара поставлена в конец порядка без подтверждённого денника.")
        elif action == "add":
            n = services.add_missing(request.user, p.pk)
            messages.success(request, f"Добавлено в конец порядка: {n}.")
        elif action == "move":
            services.move(request.user, int(request.POST["slot"]), int(request.POST["position"]))
            messages.success(request, "Порядок изменён." + (" Время стартов пересчитано." if p.timed_starts else ""))
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
        elif action == "merge":
            c = services.merge_classes(request.user, int(request.POST["tclass"]), int(request.POST["into"]))
            messages.success(request, f"Зачёт «{c.label}» объединён с «{c.merged_into.label}». Представители получили уведомление.")
        elif action == "unmerge":
            c = services.unmerge_class(request.user, int(request.POST["tclass"]))
            messages.success(request, f"Объединение зачёта «{c.label}» отменено.")
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
def pair_edit(request, pk, program_id, entry_id):
    """Судья правит данные пары (для протоколов этого турнира) и зачёт."""
    p = _program(request, pk, program_id)
    e = get_object_or_404(Entry, pk=entry_id, program=p)
    from apps.profiles.models import COLORS, RANKS, SEXES

    if request.method == "POST":
        g = request.POST.get
        year = g("birth_year", "").strip()
        hyear = g("h_birth_year", "").strip()
        try:
            services.edit_pair(request.user, e.pk,
                               {"name": " ".join(g("name", "").split()), "birth_year": int(year) if year.isdigit() else None,
                                "rank": g("rank", "").strip(), "club": g("club", "").strip(), "region": g("region", "").strip(), "coach": g("coach", "").strip()},
                               {"name": g("h_name", "").strip(), "birth_year": int(hyear) if hyear.isdigit() else None, "sex": g("h_sex", "").strip(),
                                "color": g("h_color", "").strip(), "breed": g("h_breed", "").strip()},
                               g("tclass") or None)
        except (ValueError, TournamentClass.DoesNotExist) as ex:
            messages.error(request, str(ex) or "Проверьте данные.")
        else:
            messages.success(request, "Данные пары сохранены. В опубликованном протоколе они появятся после публикации новой версии.")
            return redirect("conduct:program", pk=pk, program_id=p.pk)
    from apps.profiles.models import COLOR_SHORT, RANK_SHORT, SEX_SHORT

    return render(request, "conduct/pair_edit.html", {
        "t": p.tournament, "p": p, "e": e, "a": e.athlete_snapshot, "h": e.horse_snapshot,
        "ranks": list(dict.fromkeys(RANK_SHORT.values())), "sexes": list(SEX_SHORT.values()), "colors": list(COLOR_SHORT.values()),
        "classes": p.classes.filter(merged_into__isnull=True),
    })


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
        return redirect(reverse("conduct:program", args=[pk, p.pk]) + "?tab=panel")
    slots = list(p.slots.select_related("entry", "entry__result").order_by("position"))
    current = next((s for s in slots if s.status == StartSlot.Status.STARTED), None) or next((s for s in slots if s.status == StartSlot.Status.WAITING), None)
    upcoming = [s for s in slots if s.status == StartSlot.Status.WAITING and s != current][:4]
    done = sum(1 for s in slots if s.status not in (StartSlot.Status.WAITING, StartSlot.Status.STARTED))
    conf = services.conflicts(p.tournament, p)
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
    if not request.user.is_authenticated or not can_judge_or_manage(request.user, p.tournament):
        raise Http404  # тест 4.2: выгрузка — только судьям и организаторам этого турнира
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
    if not request.user.is_authenticated or not can_judge_or_manage(request.user, c.program.tournament):
        raise Http404
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


def can_judge_or_manage(user, t):
    from apps.tournaments.services import roles_of

    return bool(roles_of(user, t) & {"organizer", "judge"})


def _mark(row):
    return {"withdrawn": "Снят", "no_show": "Не явился"}.get(row.get("status"), "")
