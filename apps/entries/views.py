import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.phone import normalize_phone
from apps.core.models import AuditLog
from apps.profiles.models import Athlete, Horse
from apps.profiles.services import athletes_for_entry
from apps.tournaments.models import Tournament, TournamentClass
from apps.tournaments import services as tservices
from apps.tournaments.services import roles_of

from . import services
from .models import Application, ChangeProposal, Entry
from .services import Conflict, Line

# ---------------- черновик заявки (в базе) ----------------

def _cart(request, t, late):
    from .models import ApplicationDraft

    d = ApplicationDraft.objects.filter(user=request.user, tournament=t, late=late).first()
    cart = dict(d.data) if d else {}
    if "pairs" not in cart:
        cart = {"pairs": [], "lines": [], "horses": [], "key": uuid.uuid4().hex}
    return cart


def _save_cart(request, t, late, cart):
    from .models import ApplicationDraft

    if not cart["pairs"] and not cart["lines"] and not cart["horses"]:
        ApplicationDraft.objects.filter(user=request.user, tournament=t, late=late).delete()
        return
    ApplicationDraft.objects.update_or_create(user=request.user, tournament=t, late=late, defaults={"data": cart})


def _drop_cart(request, t, late):
    from .models import ApplicationDraft

    ApplicationDraft.objects.filter(user=request.user, tournament=t, late=late).delete()


def _load_lines(cart):
    ids_a = {r["a"] for r in cart["lines"]}
    ids_h = {r["h"] for r in cart["lines"]}
    ids_c = {r["c"] for r in cart["lines"]}
    athletes = Athlete.objects.in_bulk(ids_a)
    horses = Horse.objects.in_bulk(ids_h)
    classes = TournamentClass.objects.select_related("program", "merged_into").in_bulk(ids_c)
    out = []
    for raw in cart["lines"]:
        a, h, c = athletes.get(raw["a"]), horses.get(raw["h"]), classes.get(raw["c"])
        if a and h and c:
            out.append(Line(a, h, c, raw.get("ht"), bool(raw.get("cf"))))
    return out


def _programs(t):
    return list(t.programs.all().prefetch_related("classes__merged_into").order_by("day", "start_time", "position", "id"))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_choice(raw):
    """Значение выбора «зачёт|высота» из списка у маршрута."""
    if not raw:
        return None, None
    cid, _, ht = str(raw).partition("|")
    return _int(cid), _int(ht)


@never_cache  # «Назад» в браузере не показывает устаревшую форму без новой лошади
@login_required
@require_http_methods(["GET", "POST"])
def apply(request, pk):
    """APP-01…04: заявка строится из пар «всадник + лошадь»; у пары — выступления (маршрут/езда + ровно один зачёт).
    Раскрыта одна пара; маршруты для неё выбираются сразу списком и добавляются одним нажатием."""
    from apps.sport import rules

    t = get_object_or_404(Tournament, pk=pk)
    late = request.GET.get("late") == "1" or request.POST.get("late") == "1"
    reason = services.tournament_open_for(request.user, t, late)
    if reason:
        messages.error(request, reason)
        return redirect("tournaments:public", pk=t.pk)
    cart = _cart(request, t, late)
    athletes = list(athletes_for_entry(request.user))
    by_a = {a.pk: a for a in athletes}
    horse_ids = set(Horse.objects.filter(manager=request.user, archived=False, added_by_judge=False).values_list("pk", flat=True)) | set(cart["horses"])
    horses = list(Horse.objects.filter(pk__in=horse_ids).order_by("name"))
    by_h = {h.pk: h for h in horses}
    programs = _programs(t)
    classes = {c.pk: c for p in programs for c in p.classes.all()}
    year = (t.pub_starts_on or t.starts_on).year
    errors = []
    open_pair = request.GET.get("open") or request.POST.get("open") or ""

    def back(pair=None):
        url = _apply_url(t, late)
        if pair:
            url += ("&" if "?" in url else "?") + f"open={pair[0]}-{pair[1]}#pair-{pair[0]}-{pair[1]}"
        return redirect(url)

    if request.method == "POST":
        action = request.POST.get("action") or ("pick" if request.POST.get("pick_horse") else "")
        if request.POST.get("remove") is not None:
            action = "remove"
        if action == "pick":
            hid = _int(request.POST.get("horse"))
            if hid and Horse.objects.filter(pk=hid, archived=False).exists() and hid not in cart["horses"]:
                cart["horses"].append(hid)
                _save_cart(request, t, late, cart)
                messages.success(request, "Лошадь добавлена в список выбора. Она видна только в этой заявке.")
            return back()
        if action == "pair_add":
            a, h = by_a.get(_int(request.POST.get("athlete"))), by_h.get(_int(request.POST.get("horse")))
            if not a or not h:
                errors.append("Выберите всадника и лошадь.")
            elif [a.pk, h.pk] in cart["pairs"]:
                errors.append("Такая пара уже есть в заявке — выберите для неё маршруты ниже.")
                open_pair = f"{a.pk}-{h.pk}"
            else:
                cart["pairs"].append([a.pk, h.pk])
                _save_cart(request, t, late, cart)
                return back((a.pk, h.pk))
        if action == "pair_remove":
            pair = [_int(request.POST.get("a")), _int(request.POST.get("h"))]
            if pair in cart["pairs"]:
                cart["pairs"].remove(pair)
                cart["lines"] = [r for r in cart["lines"] if [r["a"], r["h"]] != pair]
                _save_cart(request, t, late, cart)
            return back()
        if action == "remove":
            try:
                raw = cart["lines"].pop(int(request.POST.get("remove") or request.POST.get("index")))
            except (ValueError, IndexError, TypeError):
                raw = None
            _save_cart(request, t, late, cart)
            return back((raw["a"], raw["h"]) if raw else None)
        if action in ("add", "add_many"):
            a, h = by_a.get(_int(request.POST.get("a"))), by_h.get(_int(request.POST.get("h")))
            picks = []
            if action == "add":  # совместимость: один зачёт
                cid, ht = _int(request.POST.get("tclass")), _int(request.POST.get("height"))
                picks.append((cid, ht, request.POST.get("confirm") == "1"))
            else:
                for p in programs:
                    chosen = [v for v in request.POST.getlist(f"sel_{p.pk}") if v]
                    multi = p.heights and len(p.heights) > 1
                    if len(chosen) > 1 and not multi:
                        errors.append(f"{p.name}: в одном маршруте пара выступает в одном зачёте — оставьте один.")
                        continue
                    for raw in chosen:  # тест 4.2: в маршруте с несколькими высотами — можно несколько высот
                        cid, ht = _parse_choice(raw)
                        if cid:
                            picks.append((cid, ht, request.POST.get(f"confirm_{p.pk}") == "1"))
            if not (a and h):
                errors.append("Пара не найдена — добавьте её заново.")
            elif not picks:
                errors.append("Выберите зачёт хотя бы в одном маршруте или езде.")
            else:
                added = []
                existing = {(r["a"], r["h"], r["c"], r.get("ht")) for r in cart["lines"]}
                for cid, ht, confirmed in picks:
                    c = classes.get(cid)
                    if not c:
                        errors.append("Зачёт не найден — обновите страницу.")
                        continue
                    if not (c.program.heights and len(c.program.heights) > 1):
                        ht = None
                    elif c.height and ht is None:
                        ht = c.height
                    line = Line(a, h, c, ht, confirmed)
                    errs = services.check_line(request.user, t, line, late=late, allowed_athletes=set(by_a))
                    if line.key() in existing:
                        errs.append("уже есть в заявке")
                    if errs:
                        errors += [f"{c.program.name}, {c.label}: {e}" for e in errs]
                        continue
                    cart["lines"].append({"a": a.pk, "h": h.pk, "c": c.pk, "ht": ht, "cf": confirmed})
                    existing.add(line.key())
                    added.append(f"{c.program.name} — {c.label}" + (f" · {ht} см" if ht and not c.height else ""))
                if added:
                    _save_cart(request, t, late, cart)
                    messages.success(request, f"{a.public_name} / {h.name}: добавлено {len(added)} — " + "; ".join(added) + ".")
                if not errors:
                    # тест 4.2: после «Добавить выбранные» выбор сворачивается, видна сводка пары
                    url = _apply_url(t, late)
                    return redirect(url + ("&" if "?" in url else "?") + f"open=-#pair-{a.pk}-{h.pk}")
                open_pair = f"{a.pk}-{h.pk}"
        if action == "submit":
            lines = _load_lines(cart)
            try:
                phone = normalize_phone(request.POST.get("contact_phone") or request.user.phone)
            except ValidationError as e:
                # тест первых пользователей: неверный телефон не подменяем молча телефоном из профиля
                errors.append("Телефон для связи: " + e.messages[0])
                phone = None
            try:
                if not phone:
                    raise ValueError(errors)
                app = services.submit(
                    request.user, t.pk, lines, cart["key"], contact_name=request.POST.get("contact_name", "").strip(),
                    contact_phone=phone, comment=request.POST.get("comment", "").strip(), late=late,
                )
            except ValueError as e:
                errors = e.args[0] if isinstance(e.args[0], list) else [str(e)]
            except Conflict as e:
                messages.error(request, str(e))
                _drop_cart(request, t, late)
                return redirect(_apply_url(t, late))
            else:
                _drop_cart(request, t, late)
                messages.success(request, f"Заявка №{app.pk} отправлена. Решение по каждому выступлению появится здесь и в уведомлениях.")
                return redirect("entries:application", pk=app.pk)

    lines = _load_lines(cart)
    pair_objs = []
    extra_h = Horse.objects.in_bulk({h for _, h in cart["pairs"]} - set(by_h))
    for i_a, i_h in cart["pairs"]:
        a, h = by_a.get(i_a), by_h.get(i_h) or extra_h.get(i_h)
        if a and h:
            pair_objs.append((a, h))
    if not open_pair and pair_objs:
        # по умолчанию раскрыта первая пара без выступлений, иначе — последняя добавленная
        empty = [(a, h) for a, h in pair_objs if not any(l.athlete.pk == a.pk and l.horse.pk == h.pk for l in lines)]
        a, h = (empty or pair_objs[-1:])[0]
        open_pair = f"{a.pk}-{h.pk}"
    pairs = []
    for a, h in pair_objs:
        mine = [(i, l) for i, l in enumerate(lines) if l.athlete.pk == a.pk and l.horse.pk == h.pk]
        is_open = open_pair == f"{a.pk}-{h.pk}"
        progs = []
        if is_open:
            for p in programs:
                opts = []
                multi = p.heights and len(p.heights) > 1
                for c in p.classes.all():
                    if c.merged_into_id:
                        continue
                    hts = [c.height] if (multi and c.height) else (p.heights if multi else [None])
                    for ht in hts:
                        why = services.class_reasons(a, h, c, year, ht, True)
                        why = [w for w in why if w != "выберите высоту"]
                        label = c.label + (f" · {ht} см" if ht and not c.height else "")
                        opts.append({"value": f"{c.pk}|{ht}" if ht else str(c.pk), "label": label, "why": why, "conditions": c.conditions})
                added = [(i, l) for i, l in mine if l.tclass.program_id == p.pk]
                progs.append({"p": p, "opts": opts, "added": added, "never": any(c.rank_rule == "never" for c in p.classes.all()),
                              "has_ok": any(not o["why"] for o in opts),
                              "need_birth": any("нужна дата рождения всадника" in (o["why"] or []) for o in opts)})
        pairs.append({"a": a, "h": h, "programs": progs, "count": len(mine), "open": is_open, "mine": mine})
    warnings = rules.entry_warnings(t, lines)
    return render(
        request, "entries/apply.html",
        {
            "t": t, "s": t.snapshot, "late": late, "pairs": pairs, "lines": lines, "errors": errors, "warnings": warnings,
            "athletes": athletes, "horses": horses, "key": cart["key"],
            # тест первых пользователей: единственный всадник/лошадь и только что созданные выбраны сразу
            "sel_a": _int(request.GET.get("athlete")) or (athletes[0].pk if len(athletes) == 1 else None),
            "sel_h": _int(request.GET.get("horse")) or (horses[0].pk if len(horses) == 1 else None),
        },
    )


def _class_choices(t):
    classes = TournamentClass.objects.filter(program__tournament=t, merged_into__isnull=True).select_related("program").order_by(
        "program__day", "program__start_time", "program__position", "height", "position")
    groups = {}
    for c in classes:
        label = f"{c.program.day:%d.%m} · {c.program.name}"
        groups.setdefault(label, []).append((c.pk, f"{c.label}{' · ' + c.conditions if c.conditions else ''}"))
    return list(groups.items()), {c.pk: c for c in classes}


def _apply_url(t, late):
    return reverse("entries:apply", args=[t.pk]) + ("?late=1" if late else "")


# ---------------- мои заявки ----------------

@login_required
def my_applications(request):
    q = request.GET.get("q", "").strip()
    decision = request.GET.get("decision", "")
    period = request.GET.get("period", "")
    apps = Application.objects.filter(submitted_by=request.user).select_related("tournament").prefetch_related("entries")
    if q:
        apps = apps.filter(Q(tournament__pub_title__icontains=q) | Q(entries__athlete_snapshot__name__icontains=q) | Q(entries__horse_snapshot__name__icontains=q)).distinct()
    if decision:
        apps = apps.filter(entries__decision=decision).distinct()
    from django.utils import timezone

    today = timezone.localdate()
    if period == "upcoming":
        apps = apps.filter(tournament__pub_ends_on__gte=today)
    elif period == "past":
        apps = apps.filter(tournament__pub_ends_on__lt=today)
    rows = [{"app": a, "summary": services.summarize(a.entries.all()), "count": len(a.entries.all())} for a in apps]
    linked = Entry.objects.filter(athlete__linked_user=request.user).exclude(application__submitted_by=request.user).exists()
    return render(request, "entries/my_list.html", {
        "rows": rows, "q": q, "decision": decision, "period": period, "linked": linked,
        "decisions": Entry.Decision.choices,
    })


@login_required
def my_starts(request):
    """Связанный спортсмен видит только собственные выступления — без группы, контактов и финансов."""
    entries = Entry.objects.filter(athlete__linked_user=request.user).select_related("tournament").order_by("-day")
    return render(request, "entries/my_starts.html", {"entries": entries})


@login_required
def application(request, pk):
    app = get_object_or_404(Application.objects.select_related("tournament", "submitted_by"), pk=pk)
    if not services.can_view_application(request.user, app):
        raise PermissionDenied
    owner = app.submitted_by_id == request.user.pk
    judge = services.can_judge(request.user, app.tournament)
    entries = list(app.entries.select_related("tclass", "athlete", "horse").prefetch_related("proposals"))
    for e in entries:
        e.pending_proposal = next((p for p in e.proposals.all() if p.state == ChangeProposal.State.PENDING), None)
    history = AuditLog.objects.filter(Q(target_type="application", target_id=app.pk) | Q(target_type="entry", target_id__in=[e.pk for e in entries])).select_related("actor")[:50]
    return render(request, "entries/application.html", {
        "app": app, "t": app.tournament, "entries": entries, "owner": owner, "judge": judge,
        "summary": services.summarize(entries), "history": history if (owner or judge) else [],
        "show_contacts": owner or judge,
    })


@login_required
@require_POST
def application_cancel(request, pk):
    app = get_object_or_404(Application, pk=pk)
    ids = request.POST.getlist("entry")
    if request.POST.get("confirm") != "1":
        entries = app.entries.filter(pk__in=ids)
        if app.submitted_by_id != request.user.pk:
            raise PermissionDenied
        if not entries:
            messages.error(request, "Выберите выступления для отмены.")
            return redirect("entries:application", pk=app.pk)
        return render(request, "entries/cancel_confirm.html", {"app": app, "entries": entries})
    items = [(int(i), request.POST.get(f"rev-{i}")) for i in ids if i.isdigit()]
    results = services.cancel_by_representative(request.user, app.pk, items)
    _report(request, results)
    return redirect("entries:application", pk=app.pk)


@login_required
@require_POST
def application_contacts(request, pk):
    app = get_object_or_404(Application, pk=pk)
    if app.submitted_by_id != request.user.pk:
        raise PermissionDenied
    try:
        app.contact_phone = normalize_phone(request.POST.get("contact_phone", ""))
    except Exception:
        messages.error(request, "Проверьте номер телефона.")
        return redirect("entries:application", pk=app.pk)
    app.contact_name = request.POST.get("contact_name", "").strip()[:200]
    app.comment = request.POST.get("comment", "").strip()
    app.revision += 1
    app.save()
    from apps.core.models import audit

    audit(request.user, "application.contacts", app)
    messages.success(request, "Контакты и комментарий обновлены.")
    return redirect("entries:application", pk=app.pk)


@login_required
@require_http_methods(["GET", "POST"])
def propose(request, pk):
    e = get_object_or_404(Entry.objects.select_related("application", "tournament", "tclass"), pk=pk)
    if e.application.submitted_by_id != request.user.pk:
        raise PermissionDenied
    athletes = list(athletes_for_entry(request.user))
    horse_ids = set(Horse.objects.filter(manager=request.user, archived=False, added_by_judge=False).values_list("pk", flat=True)) | {e.horse_id}
    horses = list(Horse.objects.filter(pk__in=horse_ids).order_by("name"))
    class_groups, classes = _class_choices(e.tournament)
    if request.method == "POST":
        a = next((x for x in athletes if str(x.pk) == request.POST.get("athlete")), None)
        h = next((x for x in horses if str(x.pk) == request.POST.get("horse")), None)
        c = classes.get(int(request.POST["tclass"])) if request.POST.get("tclass", "").isdigit() else None
        try:
            services.propose(request.user, e.pk, athlete=a, horse=h, tclass=c)
        except ValueError as ex:
            messages.error(request, str(ex))
        else:
            messages.success(request, "Предложение отправлено судье. До решения действуют прежние сведения.")
            return redirect("entries:application", pk=e.application_id)
    return render(request, "entries/propose.html", {"e": e, "athletes": athletes, "horses": horses, "class_groups": class_groups})


@login_required
@require_POST
def proposal_decide(request, pk):
    p = get_object_or_404(ChangeProposal.objects.select_related("entry"), pk=pk)
    try:
        services.decide_proposal(request.user, p.pk, request.POST.get("decision") == "accept", request.POST.get("reason", ""))
    except (ValueError, Conflict) as ex:
        messages.error(request, str(ex))
    else:
        messages.success(request, "Решение по предложению сохранено.")
    return redirect(request.POST.get("next") or reverse("entries:application", args=[p.entry.application_id]))


# ---------------- рассмотрение (судья / организатор) ----------------

def _manage_qs(t, f):
    qs = Entry.objects.filter(tournament=t)
    if f.get("q"):
        q = f["q"]
        qs = qs.filter(Q(athlete_snapshot__name__icontains=q) | Q(horse_snapshot__name__icontains=q) | Q(application__contact_name__icontains=q))
    if f.get("decision"):
        qs = qs.filter(decision=f["decision"])
    if str(f.get("program", "")).isdigit():
        qs = qs.filter(program_id=int(f["program"]))
    return qs


PAGE_SIZE = 50


TABS = [("pending", "На рассмотрении"), ("accepted", "Принятые"), ("rejected", "Отклонённые"), ("cancelled", "Отменённые")]


@login_required
def manage(request, pk):
    """Экран заявок судьи (тест 4): вкладки по решению, внутри — маршрут → зачёт → пары; принять / отклонить в строке."""
    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    from django.db.models import Count

    counts = dict(Entry.objects.filter(tournament=t).order_by().values("decision").annotate(n=Count("id")).values_list("decision", "n"))
    tab = request.GET.get("tab") or request.GET.get("decision") or ("pending" if counts.get("pending") else "accepted")
    if tab not in dict(TABS):
        tab = "pending"
    f = {"q": request.GET.get("q", ""), "program": request.GET.get("program", ""), "decision": tab}
    qs = (_manage_qs(t, f).select_related("application", "tclass", "program").prefetch_related("proposals")
          .order_by("day", "program__start_time", "program__position", "program_id", "tclass__position", "tclass_id", "athlete_snapshot__name", "pk"))
    groups, by_prog = [], {}
    for e in qs:
        e.pending_proposal = next((p for p in e.proposals.all() if p.state == ChangeProposal.State.PENDING), None)
        g = by_prog.get(e.program_id)
        if g is None:
            g = by_prog[e.program_id] = {"p": e.program, "classes": [], "by_class": {}, "n": 0}
            groups.append(g)
        c = g["by_class"].get(e.tclass_id)
        if c is None:
            c = g["by_class"][e.tclass_id] = {"c": e.tclass, "entries": []}
            g["classes"].append(c)
        c["entries"].append(e)
        g["n"] += 1
    params = "&".join(f"{k}={v}" for k, v in (("q", f["q"]), ("program", f["program"])) if v)
    return render(request, "entries/manage.html", {
        "t": t, "groups": groups, "tab": tab, "tabs": [(k, l, counts.get(k, 0)) for k, l in TABS], "f": f, "params": params,
        "programs": t.programs.all().order_by("day", "start_time", "position"), "total": sum(g["n"] for g in groups),
        "is_manager": tservices.can_manage(request.user, t),
    })


@login_required
@require_POST
def manage_decide(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    action = request.POST.get("action", "")
    back = request.POST.get("next") or reverse("entries:manage", args=[t.pk])
    if request.POST.get("scope") == "filter":
        # «Применить ко всем по фильтру»: состав берётся заново на сервере, без передачи сотен полей
        f = {k: request.POST.get(f"f_{k}", "") for k in ("q", "decision", "program")}
        qs = _manage_qs(t, f).exclude(decision=Entry.Decision.CANCELLED)
        ids = [str(i) for i in qs.values_list("pk", flat=True)]
        revs = dict(qs.values_list("pk", "revision"))
    elif request.POST.get("items"):
        ids = [c.partition(":")[0] for c in request.POST["items"].split(",") if c.partition(":")[0].isdigit()]
        revs = None
    else:
        ids = [i for i in request.POST.getlist("entry") if i.isdigit()]
        revs = None
    if not ids:
        messages.error(request, "Выберите строки — отметьте их галочкой или нажмите «Выбрать все по фильтру».")
        return redirect(back)
    if request.POST.get("confirm") != "1":
        entries = list(Entry.objects.filter(tournament=t, pk__in=ids).order_by("day", "program_id", "pk"))
        # состав и версии строк уходят одним полем «id:версия,…» — без лимита на число полей формы
        items = ",".join(f"{e.pk}:{request.POST.get(f'rev-{e.pk}') or (revs or {}).get(e.pk) or e.revision}" for e in entries)
        return render(request, "entries/decide_confirm.html", {"t": t, "entries": entries[:100], "more": max(0, len(entries) - 100),
                                                              "count": len(entries), "items": items, "action": action,
                                                              "label": services.ACTIONS.get(action, ""), "next": back})
    try:
        if request.POST.get("items"):
            items = []
            for chunk in request.POST["items"].split(","):
                i, _, rev = chunk.partition(":")
                if i.isdigit():
                    items.append((int(i), rev))
        else:
            items = [(int(i), request.POST.get(f"rev-{i}") or (revs or {}).get(int(i))) for i in ids]
        results = services.decide(request.user, t.pk, items, action, request.POST.get("reason", ""))
    except ValueError as ex:
        messages.error(request, str(ex))
        return redirect(back)
    _report(request, results)
    return redirect(back)


def _report(request, results):
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    if len(results) == 1 and ok:
        e = Entry.objects.filter(pk=ok[0].entry_id).first()
        who = f": {e.athlete_snapshot.get('name')} / {e.horse_snapshot.get('name')} — {e.program_name} · {e.class_name}" if e else ""
        messages.success(request, f"{ok[0].text}{who}.")
    elif ok:
        messages.success(request, f"Выполнено для {len(ok)} из {len(results)}.")
    reasons = {}
    for r in bad:
        reasons.setdefault(r.text, []).append(r.entry_id)
    for text, ids in reasons.items():
        tail = ", ".join(f"#{i}" for i in ids[:5]) + (f" и ещё {len(ids) - 5}" if len(ids) > 5 else "")
        messages.error(request, f"Не выполнено для {len(ids)}: {text} ({tail}).")


# ---------------- быстрое добавление пары судьёй (тест 4) ----------------

@login_required
def quick_add(request, pk):
    from .forms import QuickAddForm

    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    nxt = request.GET.get("next") or request.POST.get("next") or ""
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else ""
    program = request.GET.get("program", "")
    initial = {}
    if program.isdigit():
        initial["classes"] = [str(c) for c in TournamentClass.objects.filter(program_id=int(program), program__tournament=t,
                                                                            merged_into__isnull=True).values_list("pk", flat=True)[:1]]
    form = QuickAddForm(t, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            entries = services.quick_add(request.user, t.pk, form.cleaned_data)
        except ValueError as e:
            messages.error(request, str(e))
        else:
            e = entries[0]
            messages.success(request, f"Добавлено: {e.athlete_snapshot.get('name')} / {e.horse_snapshot.get('name')} — "
                                      + ", ".join(f"{x.program_name} · {x.class_name}" for x in entries) + ". Выступления приняты.")
            if "more" in request.POST:
                return redirect(reverse("entries:quick_add", args=[t.pk]) + (f"?next={nxt}" if nxt else ""))
            return redirect(nxt or reverse("entries:manage", args=[t.pk]) + "?tab=accepted")
    groups = {}
    for k, c in form.class_objs.items():
        groups.setdefault(c.program, []).append((k, c))
    selected = form["classes"].value() or []
    return render(request, "entries/quick_add.html", {"t": t, "form": form, "groups": list(groups.items()), "selected": [str(x) for x in selected], "next": nxt})


@login_required
def quick_search(request, pk):
    """Поиск спортсмена или лошади по всей базе для быстрого добавления (только судьям турнира)."""
    from django.http import JsonResponse

    from apps.profiles.models import Athlete, Horse

    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"items": []})
    words = q.split()
    if request.GET.get("kind") == "horse":
        qs = Horse.objects.filter(archived=False, name__icontains=q)[:10]
        items = [{"id": h.pk, "title": h.name, "sub": h.summary()} for h in qs]
    else:
        qs = Athlete.objects.filter(archived=False)
        for w in words:
            qs = qs.filter(Q(last_name__icontains=w) | Q(first_name__icontains=w))
        items = [{"id": a.pk, "title": a.full_name, "sub": " · ".join(x for x in (str(a.birth_year or ""), a.rank_short, a.club) if x)}
                 for a in qs.order_by("last_name", "first_name")[:10]]
    return JsonResponse({"items": items})
