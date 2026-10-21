import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.phone import normalize_phone
from apps.core.models import AuditLog
from apps.profiles.models import Athlete, Horse
from apps.profiles.services import athletes_for_entry
from apps.tournaments.models import Tournament, TournamentClass
from apps.tournaments.services import roles_of

from . import services
from .models import Application, ChangeProposal, Entry
from .services import Conflict, Line

# ---------------- корзина заявки (в сессии) ----------------

def _cart_key(t, late):
    return f"cart:{t.pk}:{int(late)}"


def _cart(request, t, late):
    cart = request.session.get(_cart_key(t, late))
    if not cart or "pairs" not in cart:
        cart = {"pairs": [], "lines": [], "horses": [], "key": uuid.uuid4().hex}
    return cart


def _save_cart(request, t, late, cart):
    request.session[_cart_key(t, late)] = cart
    request.session.modified = True


def _load_lines(cart):
    out = []
    for raw in cart["lines"]:
        try:
            line = Line(
                Athlete.objects.get(pk=raw["a"]), Horse.objects.get(pk=raw["h"]),
                TournamentClass.objects.select_related("program", "merged_into").get(pk=raw["c"]),
                raw.get("ht"), bool(raw.get("cf")),
            )
        except (Athlete.DoesNotExist, Horse.DoesNotExist, TournamentClass.DoesNotExist):
            continue
        out.append(line)
    return out


def _programs(t):
    return list(t.programs.all().prefetch_related("classes").order_by("day", "start_time", "position", "id"))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@login_required
@require_http_methods(["GET", "POST"])
def apply(request, pk):
    """APP-01…04: заявка строится из пар «всадник + лошадь»; у пары — выступления (маршрут/езда + ровно один зачёт)."""
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
    horse_ids = set(Horse.objects.filter(manager=request.user, archived=False).values_list("pk", flat=True)) | set(cart["horses"])
    horses = list(Horse.objects.filter(pk__in=horse_ids).order_by("name"))
    by_h = {h.pk: h for h in horses}
    programs = _programs(t)
    classes = {c.pk: c for p in programs for c in p.classes.all()}
    year = (t.pub_starts_on or t.starts_on).year
    errors = []

    if request.method == "POST":
        action = request.POST.get("action") or ("pick" if request.POST.get("pick_horse") else "")
        if action == "pick":
            hid = _int(request.POST.get("horse"))
            if hid and Horse.objects.filter(pk=hid, archived=False).exists() and hid not in cart["horses"]:
                cart["horses"].append(hid)
                _save_cart(request, t, late, cart)
                messages.success(request, "Лошадь добавлена в список выбора. Она видна только в этой заявке.")
            return redirect(_apply_url(t, late))
        if action == "pair_add":
            a, h = by_a.get(_int(request.POST.get("athlete"))), by_h.get(_int(request.POST.get("horse")))
            if not a or not h:
                errors.append("Выберите всадника и лошадь.")
            elif [a.pk, h.pk] in cart["pairs"]:
                errors.append("Такая пара уже есть в заявке.")
            else:
                cart["pairs"].append([a.pk, h.pk])
                _save_cart(request, t, late, cart)
                return redirect(_apply_url(t, late) + f"#pair-{a.pk}-{h.pk}")
        if action == "pair_remove":
            pair = [_int(request.POST.get("a")), _int(request.POST.get("h"))]
            if pair in cart["pairs"]:
                cart["pairs"].remove(pair)
                cart["lines"] = [r for r in cart["lines"] if [r["a"], r["h"]] != pair]
                _save_cart(request, t, late, cart)
            return redirect(_apply_url(t, late))
        if action == "remove":
            try:
                cart["lines"].pop(int(request.POST.get("index")))
            except (ValueError, IndexError, TypeError):
                pass
            _save_cart(request, t, late, cart)
            return redirect(_apply_url(t, late))
        if action == "add":
            a, h = by_a.get(_int(request.POST.get("a"))), by_h.get(_int(request.POST.get("h")))
            c = classes.get(_int(request.POST.get("tclass")))
            ht = _int(request.POST.get("height"))
            if c and not (c.program.heights and len(c.program.heights) > 1):
                ht = None
            if c and c.height and ht is None:
                ht = c.height
            if not (a and h):
                errors.append("Пара не найдена — добавьте её заново.")
            elif not c:
                errors.append("Выберите зачёт.")
            else:
                line = Line(a, h, c, ht, request.POST.get("confirm") == "1")
                errors = services.check_line(request.user, t, line, late=late, allowed_athletes=set(by_a))
                if any((r["a"], r["h"], r["c"], r.get("ht")) == line.key() for r in cart["lines"]):
                    errors.append("Такое выступление уже есть в заявке.")
                if not errors:
                    cart["lines"].append({"a": a.pk, "h": h.pk, "c": c.pk, "ht": ht, "cf": line.confirmed})
                    _save_cart(request, t, late, cart)
                    messages.success(request, f"Добавлено: {a.public_name} / {h.name} — {c.program.name}, {c.label}.")
                    return redirect(_apply_url(t, late) + f"#pair-{a.pk}-{h.pk}")
        if action == "submit":
            lines = _load_lines(cart)
            try:
                phone = normalize_phone(request.POST.get("contact_phone") or request.user.phone)
            except Exception:
                phone = request.user.phone
            try:
                app = services.submit(
                    request.user, t.pk, lines, cart["key"], contact_name=request.POST.get("contact_name", "").strip(),
                    contact_phone=phone, comment=request.POST.get("comment", "").strip(), late=late,
                )
            except ValueError as e:
                errors = e.args[0] if isinstance(e.args[0], list) else [str(e)]
            except Conflict as e:
                messages.error(request, str(e))
                request.session.pop(_cart_key(t, late), None)
                return redirect(_apply_url(t, late))
            else:
                request.session.pop(_cart_key(t, late), None)
                messages.success(request, f"Заявка №{app.pk} отправлена. Решение по каждому выступлению появится здесь и в уведомлениях.")
                return redirect("entries:application", pk=app.pk)

    lines = _load_lines(cart)
    pairs = []
    for i_a, i_h in cart["pairs"]:
        a, h = by_a.get(i_a), Horse.objects.filter(pk=i_h).first()
        if not a or not h:
            continue
        mine = [(i, l) for i, l in enumerate(lines) if l.athlete.pk == a.pk and l.horse.pk == h.pk]
        progs = []
        for p in programs:
            opts = []
            for c in p.classes.all():
                if c.merged_into_id:
                    continue
                why = services.class_reasons(a, h, c, year, c.height, True)
                why = [w for w in why if w != "выберите высоту" and not w.startswith("зачёт для высоты")]
                opts.append({"c": c, "why": why})
            progs.append({"p": p, "opts": opts, "added": [(i, l) for i, l in mine if l.tclass.program_id == p.pk],
                          "never": any(o["c"].rank_rule == "never" for o in opts)})
        pairs.append({"a": a, "h": h, "programs": progs, "count": len(mine)})
    warnings = rules.entry_warnings(t, lines)
    return render(
        request, "entries/apply.html",
        {
            "t": t, "s": t.snapshot, "late": late, "pairs": pairs, "lines": lines, "errors": errors, "warnings": warnings,
            "athletes": athletes, "horses": horses, "key": cart["key"],
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
    horse_ids = set(Horse.objects.filter(manager=request.user, archived=False).values_list("pk", flat=True)) | {e.horse_id}
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

@login_required
def manage(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    qs = Entry.objects.filter(tournament=t).select_related("application", "tclass").prefetch_related("proposals")
    f = {k: request.GET.get(k, "") for k in ("q", "decision", "program")}
    if f["q"]:
        qs = qs.filter(Q(athlete_snapshot__name__icontains=f["q"]) | Q(horse_snapshot__name__icontains=f["q"]) | Q(application__contact_name__icontains=f["q"]))
    if f["decision"]:
        qs = qs.filter(decision=f["decision"])
    if f["program"].isdigit():
        qs = qs.filter(program_id=int(f["program"]))
    entries = list(qs)
    for e in entries:
        e.pending_proposal = next((p for p in e.proposals.all() if p.state == ChangeProposal.State.PENDING), None)
    return render(request, "entries/manage.html", {
        "t": t, "entries": entries, "f": f, "programs": t.programs.all(), "actions": services.ACTIONS,
        "decisions": Entry.Decision.choices,
        "counts": services.summarize(Entry.objects.filter(tournament=t)),
    })


@login_required
@require_POST
def manage_decide(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    ids = [i for i in request.POST.getlist("entry") if i.isdigit()]
    action = request.POST.get("action", "")
    if not ids:
        messages.error(request, "Выберите строки.")
        return redirect(request.POST.get("next") or reverse("entries:manage", args=[t.pk]))
    if request.POST.get("confirm") != "1":
        entries = Entry.objects.filter(tournament=t, pk__in=ids)
        return render(request, "entries/decide_confirm.html", {"t": t, "entries": entries, "action": action, "label": services.ACTIONS.get(action, ""), "next": request.POST.get("next", "")})
    try:
        results = services.decide(request.user, t.pk, [(int(i), request.POST.get(f"rev-{i}")) for i in ids], action, request.POST.get("reason", ""))
    except ValueError as ex:
        messages.error(request, str(ex))
        return redirect(request.POST.get("next") or reverse("entries:manage", args=[t.pk]))
    _report(request, results)
    return redirect(request.POST.get("next") or reverse("entries:manage", args=[t.pk]))


def _report(request, results):
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    if ok:
        messages.success(request, f"Выполнено для {len(ok)} из {len(results)}.")
    for r in bad:
        messages.error(request, f"Позиция #{r.entry_id}: {r.text}.")
