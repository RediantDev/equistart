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
from .services import Conflict, Line, PriceChanged

MAX_PARTNERS = 3


# ---------------- корзина заявки (в сессии) ----------------

def _cart_key(t, late):
    return f"cart:{t.pk}:{int(late)}"


def _cart(request, t, late):
    cart = request.session.get(_cart_key(t, late))
    if not cart:
        cart = {"lines": [], "horses": [], "key": uuid.uuid4().hex}
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
                TournamentClass.objects.select_related("program").get(pk=raw["c"]),
                partners=[(Athlete.objects.get(pk=a), Horse.objects.get(pk=h)) for a, h in raw.get("p", [])],
                fee_seen=raw.get("fee"),
            )
        except (Athlete.DoesNotExist, Horse.DoesNotExist, TournamentClass.DoesNotExist):
            continue
        out.append(line)
    return out


def _class_choices(t):
    classes = TournamentClass.objects.filter(program__tournament=t).select_related("program").order_by("program__day", "program__start_time", "program__position", "position")
    groups = {}
    for c in classes:
        label = f"{c.program.day:%d.%m} · {c.program.name}"
        groups.setdefault(label, []).append((c.pk, f"{c.name}{' · ' + c.conditions if c.conditions else ''}{' · ' + c.get_composition_display().lower() if c.composition != 'single' else ''}"))
    return list(groups.items()), {c.pk: c for c in classes}


@login_required
@require_http_methods(["GET", "POST"])
def apply(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    late = request.GET.get("late") == "1" or request.POST.get("late") == "1"
    reason = services.tournament_open_for(request.user, t, late)
    if reason:
        messages.error(request, reason)
        return redirect("tournaments:public", pk=t.pk)
    cart = _cart(request, t, late)
    athletes = list(athletes_for_entry(request.user))
    horse_ids = set(Horse.objects.filter(manager=request.user, archived=False).values_list("pk", flat=True)) | set(cart["horses"])
    horses = list(Horse.objects.filter(pk__in=horse_ids).order_by("name"))
    class_groups, classes = _class_choices(t)
    errors = []
    add_values = {}

    if request.method == "POST":
        action = request.POST.get("action") or ("pick" if request.POST.get("pick_horse") else "")
        if action == "pick":
            hid = request.POST.get("horse")
            if hid and Horse.objects.filter(pk=hid, archived=False).exists() and int(hid) not in cart["horses"]:
                cart["horses"].append(int(hid))
                _save_cart(request, t, late, cart)
                messages.success(request, "Лошадь добавлена в список выбора. Она видна только в этой заявке.")
            return redirect(_apply_url(t, late))
        if action == "remove":
            try:
                cart["lines"].pop(int(request.POST.get("index")))
            except (ValueError, IndexError):
                pass
            _save_cart(request, t, late, cart)
            return redirect(_apply_url(t, late))
        if action == "add":
            add_values = request.POST
            line, errors = _parse_line(request, t, athletes, horse_ids, classes)
            if line and not errors:
                errors = services.check_line(request.user, t, line, late=late, allowed_athletes={a.pk for a in athletes})
                if any((r["a"], r["h"], r["c"], tuple(tuple(x) for x in r.get("p", []))) == line.key() for r in cart["lines"]):
                    errors.append("Такое выступление уже есть в заявке.")
            if not errors:
                cart["lines"].append({"a": line.athlete.pk, "h": line.horse.pk, "c": line.tclass.pk, "p": [[a.pk, h.pk] for a, h in line.partners], "fee": line.tclass.fee_kopecks})
                _save_cart(request, t, late, cart)
                messages.success(request, "Выступление добавлено в заявку.")
                return redirect(_apply_url(t, late))
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
            except PriceChanged as pc:
                for raw in cart["lines"]:
                    raw["fee"] = classes[raw["c"]].fee_kopecks if raw["c"] in classes else raw.get("fee")
                _save_cart(request, t, late, cart)
                messages.error(request, "Организатор изменил цену некоторых зачётов. Проверьте суммы и отправьте заявку ещё раз.")
                return redirect(_apply_url(t, late))
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
    total = sum(l.tclass.fee_kopecks for l in lines)
    return render(
        request, "entries/apply.html",
        {
            "t": t, "s": t.snapshot, "late": late, "lines": list(enumerate(lines)), "total": total, "errors": errors,
            "athletes": athletes, "horses": horses, "class_groups": class_groups, "add": add_values,
            "partner_range": range(1, MAX_PARTNERS + 1), "key": cart["key"],
        },
    )


def _apply_url(t, late):
    return reverse("entries:apply", args=[t.pk]) + ("?late=1" if late else "")


def _parse_line(request, t, athletes, horse_ids, classes):
    errors = []
    by_id = {a.pk: a for a in athletes}

    def athlete(field):
        v = request.POST.get(field)
        return by_id.get(int(v)) if v and v.isdigit() else None

    def horse(field):
        v = request.POST.get(field)
        if v and v.isdigit() and int(v) in horse_ids:
            return Horse.objects.filter(pk=int(v)).first()
        return None

    a, h = athlete("athlete"), horse("horse")
    cid = request.POST.get("tclass")
    c = classes.get(int(cid)) if cid and cid.isdigit() else None
    if not a:
        errors.append("Выберите спортсмена.")
    if not h:
        errors.append("Выберите лошадь.")
    if not c:
        errors.append("Выберите программу и зачёт.")
    partners = []
    for i in range(1, MAX_PARTNERS + 1):
        pa, ph = athlete(f"partner_athlete_{i}"), horse(f"partner_horse_{i}")
        if pa and ph:
            partners.append((pa, ph))
        elif pa or ph:
            errors.append(f"Партнёр {i}: выберите и спортсмена, и лошадь.")
    if errors:
        return None, errors
    return Line(a, h, c, partners=partners), []


# ---------------- мои заявки ----------------

@login_required
def my_applications(request):
    q = request.GET.get("q", "").strip()
    decision = request.GET.get("decision", "")
    admission = request.GET.get("admission", "")
    period = request.GET.get("period", "")
    apps = Application.objects.filter(submitted_by=request.user).select_related("tournament").prefetch_related("entries")
    if q:
        apps = apps.filter(Q(tournament__pub_title__icontains=q) | Q(entries__athlete_snapshot__name__icontains=q) | Q(entries__horse_snapshot__name__icontains=q)).distinct()
    if decision:
        apps = apps.filter(entries__decision=decision).distinct()
    if admission:
        apps = apps.filter(entries__admission=admission).distinct()
    from django.utils import timezone

    today = timezone.localdate()
    if period == "upcoming":
        apps = apps.filter(tournament__pub_ends_on__gte=today)
    elif period == "past":
        apps = apps.filter(tournament__pub_ends_on__lt=today)
    rows = [{"app": a, "summary": services.summarize(a.entries.all()), "count": len(a.entries.all())} for a in apps]
    linked = Entry.objects.filter(athlete__linked_user=request.user).exclude(application__submitted_by=request.user).exists()
    return render(request, "entries/my_list.html", {
        "rows": rows, "q": q, "decision": decision, "admission": admission, "period": period, "linked": linked,
        "decisions": Entry.Decision.choices, "admissions": Entry.Admission.choices,
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
    entries = list(app.entries.select_related("tclass", "athlete", "horse").prefetch_related("proposals", "partners"))
    for e in entries:
        e.pending_proposal = next((p for p in e.proposals.all() if p.state == ChangeProposal.State.PENDING), None)
    history = AuditLog.objects.filter(Q(target_type="application", target_id=app.pk) | Q(target_type="entry", target_id__in=[e.pk for e in entries])).select_related("actor")[:50]
    charges = None
    try:
        from apps.finance.services import application_balance

        charges = application_balance(app)
    except ImportError:
        pass
    return render(request, "entries/application.html", {
        "app": app, "t": app.tournament, "entries": entries, "owner": owner, "judge": judge,
        "summary": services.summarize(entries), "history": history if (owner or judge) else [], "charges": charges,
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
    f = {k: request.GET.get(k, "") for k in ("q", "decision", "admission", "program")}
    if f["q"]:
        qs = qs.filter(Q(athlete_snapshot__name__icontains=f["q"]) | Q(horse_snapshot__name__icontains=f["q"]) | Q(application__contact_name__icontains=f["q"]))
    if f["decision"]:
        qs = qs.filter(decision=f["decision"])
    if f["admission"]:
        qs = qs.filter(admission=f["admission"])
    if f["program"].isdigit():
        qs = qs.filter(program_id=int(f["program"]))
    entries = list(qs)
    for e in entries:
        e.pending_proposal = next((p for p in e.proposals.all() if p.state == ChangeProposal.State.PENDING), None)
    return render(request, "entries/manage.html", {
        "t": t, "entries": entries, "f": f, "programs": t.programs.all(), "actions": services.ACTIONS,
        "decisions": Entry.Decision.choices, "admissions": Entry.Admission.choices,
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
