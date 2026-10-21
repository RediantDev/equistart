import datetime
import zoneinfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.models import AuditLog, audit

from . import services
from .disciplines import DISCIPLINE_CHOICES, DISCIPLINE_COLORS
from .forms import BasicsForm, ClassForm, InviteForm, ProgramForm, TermsForm
from .models import ProgramItem, TeamMember, Tournament, TournamentClass, Venue
from .regions import REGION_CHOICES, REGION_NAMES, tz_label
from .services import Conflict

STEPS = [
    (1, "Основные сведения"),
    (2, "Дисциплины и программа"),
    (3, "Зачёты и цены"),
    (4, "Положение и условия"),
    (5, "Проверка и отправка"),
]
CONFLICT_MSG = "Данные турнира изменились в другой вкладке или другим пользователем. Страница обновлена — проверьте и повторите."


# ---------------- общие помощники ----------------

def _managed(request, pk, lock=False) -> Tournament:
    qs = Tournament.objects.select_related("venue")
    if lock:
        qs = qs.select_for_update(of=("self",))
    t = get_object_or_404(qs, pk=pk)
    services.require_manage(request.user, t)
    return t


def _editable_or_redirect(request, t):
    if t.is_locked:
        messages.error(request, "Турнир на проверке у модератора — редактирование откроется после решения.")
        return redirect("tournaments:step", pk=t.pk, step=5)
    return None


def _step_state(t):
    """Номер первого незаполненного шага и признаки готовности шагов."""
    probs = services.problems(t)
    bad = {p.step for p in probs}
    return probs, bad


def _wizard_ctx(t, step, **extra):
    probs, bad = _step_state(t)
    first_bad = min(bad) if bad else 6
    steps = [
        {"n": n, "name": name, "done": n not in bad and n < 5, "current": n == step, "reachable": n <= first_bad or n <= step}
        for n, name in STEPS
    ]
    ctx = {"t": t, "step": step, "steps": steps, "problems": probs, "first_bad": first_bad}
    ctx.update(extra)
    return ctx


# ---------------- мои соревнования ----------------

@login_required
def my_tournaments(request):
    user = request.user
    team_ids = TeamMember.objects.filter(user=user, state=TeamMember.State.ACCEPTED).values_list("tournament_id", flat=True)
    items = (
        Tournament.objects.filter(Q(owner=user) | Q(pk__in=team_ids))
        .select_related("venue")
        .distinct()
        .order_by("-starts_on", "-created_at")
    )
    rows = [{"t": t, "roles": services.roles_of(user, t)} for t in items]
    return render(request, "tournaments/my_list.html", {"rows": rows})


@login_required
@require_http_methods(["GET", "POST"])
def create(request):
    if not request.user.can_create_tournament:
        messages.info(request, "Чтобы создавать соревнования, добавьте роль «Организатор», «Судья» или «Клуб».")
        return redirect("accounts:add_role")
    last = Tournament.objects.filter(owner=request.user).select_related("venue").order_by("-created_at").first()
    initial = {"organizer_name": last.organizer_name, "contact": last.contact, "venue": last.venue_id} if last else {}
    form = BasicsForm(request.user, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            t = Tournament(owner=request.user)
            _apply_basics(request, t, form)
            t.save()
            audit(request.user, "tournament.create", t)
        messages.success(request, "Черновик создан. Теперь добавьте программу.")
        return redirect("tournaments:step", pk=t.pk, step=2)
    return render(request, "tournaments/step1.html", {"form": form, "t": None, "step": 1, "steps": [
        {"n": n, "name": name, "done": False, "current": n == 1, "reachable": n == 1} for n, name in STEPS
    ]})


def _apply_basics(request, t, form):
    c = form.cleaned_data
    venue = c.get("venue")
    if not venue:
        venue = Venue.objects.create(
            name=c["venue_name"].strip(),
            region=c["venue_region"],
            settlement=c["venue_settlement"].strip(),
            address=c.get("venue_address", "").strip(),
            timezone=c["venue_tz"],
            created_by=request.user,
        )
    t.title = c["title"].strip()
    t.starts_on = c["starts_on"]
    t.ends_on = c["ends_on"]
    t.venue = venue
    t.timezone = venue.timezone
    t.organizer_name = c["organizer_name"].strip()
    t.contact = c.get("contact", "").strip()


# ---------------- шаги мастера ----------------

@login_required
@require_http_methods(["GET", "POST"])
def step(request, pk, step):
    if step not in (1, 2, 3, 4, 5):
        raise Http404
    t = _managed(request, pk)
    if request.method == "GET" and step not in (5,) and not t.is_locked:
        probs, bad = _step_state(t)
        first_bad = min(bad) if bad else 6
        if step > first_bad:
            messages.error(request, "Сначала заполните предыдущий шаг.")
            return redirect("tournaments:step", pk=t.pk, step=first_bad)
    if step == 1:
        return _step1(request, t)
    if step == 2:
        return render(request, "tournaments/step2.html", _wizard_ctx(t, 2, **_step2_ctx(t)))
    if step == 3:
        return render(request, "tournaments/step3.html", _wizard_ctx(t, 3, **_step3_ctx(t)))
    if step == 4:
        return _step4(request, t)
    return _step5(request, t)


def _step1(request, t):
    if request.method == "POST":
        if (r := _editable_or_redirect(request, t)):
            return r
        form = BasicsForm(request.user, request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    t = _managed(request, t.pk, lock=True)
                    services.check_revision(t, request.POST.get("revision"))
                    _apply_basics(request, t, form)
                    t.mark_changed()
                    t.save()
                    audit(request.user, "tournament.edit", t, step=1)
            except Conflict:
                messages.error(request, CONFLICT_MSG)
                return redirect("tournaments:step", pk=t.pk, step=1)
            nxt = 2 if "next" in request.POST else 1
            messages.success(request, "Сохранено.")
            return redirect("tournaments:step", pk=t.pk, step=nxt)
    else:
        form = BasicsForm(
            request.user,
            initial={
                "title": t.title, "starts_on": t.starts_on, "ends_on": t.ends_on, "multiday": t.is_multiday,
                "venue": t.venue_id, "organizer_name": t.organizer_name, "contact": t.contact,
            },
        )
    return render(request, "tournaments/step1.html", _wizard_ctx(t, 1, form=form))


def _step2_ctx(t, form=None, editing=None):
    days = t.days()
    programs = list(t.programs.all().prefetch_related("classes"))
    by_day = [{"day": d, "programs": [p for p in programs if p.day == d]} for d in days]
    orphans = [p for p in programs if p.day not in days]
    return {
        "by_day": by_day,
        "orphans": orphans,
        "form": form or ProgramForm(t, initial={"day": days[0].isoformat()} if days else None),
        "editing": editing,
        "discipline_colors": DISCIPLINE_COLORS,
    }


@login_required
@require_POST
def program_save(request, pk, item_id=None):
    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    instance = get_object_or_404(ProgramItem, pk=item_id, tournament=t) if item_id else None
    form = ProgramForm(t, request.POST, instance=instance)
    if not form.is_valid():
        return render(request, "tournaments/step2.html", _wizard_ctx(t, 2, **_step2_ctx(t, form=form, editing=instance)), status=422)
    try:
        with transaction.atomic():
            locked = _managed(request, pk, lock=True)
            services.check_revision(locked, request.POST.get("revision"))
            obj = form.save(commit=False)
            if not instance:
                obj.position = locked.programs.count()
            obj.save()
            locked.mark_changed()
            locked.save()
            audit(request.user, "program.save", locked, program=obj.pk, name=obj.name)
    except Conflict:
        messages.error(request, CONFLICT_MSG)
        return redirect("tournaments:step", pk=pk, step=2)
    messages.success(request, f"Программа «{obj.name}» сохранена.")
    return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + f"#program-{obj.pk}")


@login_required
@require_GET
def program_edit(request, pk, item_id):
    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    item = get_object_or_404(ProgramItem, pk=item_id, tournament=t)
    form = ProgramForm(t, instance=item, initial={"day": item.day.isoformat()})
    return render(request, "tournaments/step2.html", _wizard_ctx(t, 2, **_step2_ctx(t, form=form, editing=item)))


@login_required
@require_POST
def program_delete(request, pk, item_id):
    try:
        with transaction.atomic():
            t = _managed(request, pk, lock=True)
            if t.is_locked:
                raise Conflict
            services.check_revision(t, request.POST.get("revision"))
            item = get_object_or_404(ProgramItem, pk=item_id, tournament=t)
            name = item.name
            item.delete()
            t.mark_changed()
            t.save()
            audit(request.user, "program.delete", t, name=name)
    except Conflict:
        messages.error(request, CONFLICT_MSG)
        return redirect("tournaments:step", pk=pk, step=2)
    except ProtectedError:
        messages.error(request, "На эту программу уже поданы заявки — удалить её нельзя. Отмените заявки или измените программу.")
        return redirect("tournaments:step", pk=pk, step=2)
    messages.success(request, f"Программа «{name}» удалена вместе с её зачётами.")
    return redirect("tournaments:step", pk=pk, step=2)


def _step3_ctx(t, form=None, form_program=None, editing=None):
    programs = list(t.programs.all().prefetch_related("classes"))
    for p in programs:
        if form is not None and form_program is not None and p.pk == form_program.pk:
            p.form = form
        else:
            p.form = ClassForm(p)
    return {"programs": programs, "editing": editing}


@login_required
@require_POST
def class_save(request, pk, program_id, class_id=None):
    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    program = get_object_or_404(ProgramItem, pk=program_id, tournament=t)
    instance = get_object_or_404(TournamentClass, pk=class_id, program=program) if class_id else None
    form = ClassForm(program, request.POST, instance=instance)
    if not form.is_valid():
        return render(
            request, "tournaments/step3.html",
            _wizard_ctx(t, 3, **_step3_ctx(t, form=form, form_program=program, editing=instance)), status=422,
        )
    try:
        with transaction.atomic():
            locked = _managed(request, pk, lock=True)
            services.check_revision(locked, request.POST.get("revision"))
            obj = form.save(commit=False)
            if not instance:
                obj.position = program.classes.count()
            obj.save()
            locked.mark_changed()
            locked.save()
            audit(request.user, "class.save", locked, tclass=obj.pk, name=obj.name)
    except Conflict:
        messages.error(request, CONFLICT_MSG)
        return redirect("tournaments:step", pk=pk, step=3)
    messages.success(request, f"Зачёт «{obj.name}» сохранён.")
    return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 3}) + f"#program-{program.pk}")


@login_required
@require_GET
def class_edit(request, pk, program_id, class_id):
    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    program = get_object_or_404(ProgramItem, pk=program_id, tournament=t)
    item = get_object_or_404(TournamentClass, pk=class_id, program=program)
    form = ClassForm(program, instance=item)
    return render(request, "tournaments/step3.html", _wizard_ctx(t, 3, **_step3_ctx(t, form=form, form_program=program, editing=item)))


@login_required
@require_POST
def class_delete(request, pk, program_id, class_id):
    try:
        with transaction.atomic():
            t = _managed(request, pk, lock=True)
            if t.is_locked:
                raise Conflict
            services.check_revision(t, request.POST.get("revision"))
            item = get_object_or_404(TournamentClass, pk=class_id, program__tournament=t, program_id=program_id)
            name = item.name
            item.delete()
            t.mark_changed()
            t.save()
            audit(request.user, "class.delete", t, name=name)
    except Conflict:
        messages.error(request, CONFLICT_MSG)
        return redirect("tournaments:step", pk=pk, step=3)
    except ProtectedError:
        messages.error(request, "В этот зачёт уже поданы заявки — удалить его нельзя.")
        return redirect("tournaments:step", pk=pk, step=3)
    messages.success(request, f"Зачёт «{name}» удалён.")
    return redirect("tournaments:step", pk=pk, step=3)


def _step4(request, t):
    if request.method == "POST":
        if (r := _editable_or_redirect(request, t)):
            return r
        form = TermsForm(request.POST, request.FILES, instance=t)
        if form.is_valid():
            try:
                with transaction.atomic():
                    locked = _managed(request, t.pk, lock=True)
                    services.check_revision(locked, request.POST.get("revision"))
                    locked.entry_deadline = form.cleaned_data["entry_deadline"]
                    locked.cancel_terms = form.cleaned_data["cancel_terms"]
                    new_file = request.FILES.get("regulation")
                    if new_file:
                        # Старый файл не удаляем: на него может ссылаться опубликованная версия.
                        locked.regulation = form.cleaned_data["regulation"]
                    elif form.cleaned_data.get("regulation") is False:
                        locked.regulation = None
                    locked.mark_changed()
                    locked.save()
                    audit(request.user, "tournament.edit", locked, step=4, new_regulation=bool(new_file))
            except Conflict:
                messages.error(request, CONFLICT_MSG)
                return redirect("tournaments:step", pk=t.pk, step=4)
            messages.success(request, "Сохранено.")
            return redirect("tournaments:step", pk=t.pk, step=5 if "next" in request.POST else 4)
    else:
        form = TermsForm(instance=t)
    return render(request, "tournaments/step4.html", _wizard_ctx(t, 4, form=form))


def _step5(request, t):
    if request.method == "POST":
        try:
            services.submit(request.user, t.pk, request.POST.get("revision"))
        except Conflict:
            messages.error(request, CONFLICT_MSG)
        except ValueError:
            messages.error(request, "Не всё заполнено — исправьте пункты ниже.")
        else:
            messages.success(request, "Турнир отправлен на проверку. Мы сообщим о решении модератора в кабинете.")
        return redirect("tournaments:step", pk=t.pk, step=5)
    preview = services.build_snapshot(t) if t.starts_on else None
    history = AuditLog.objects.filter(target_type="tournament", target_id=t.pk).select_related("actor")[:20]
    return render(request, "tournaments/step5.html", _wizard_ctx(t, 5, preview=preview, history=history, tz_label=tz_label(t.timezone) if t.timezone else ""))


@login_required
@require_POST
def delete(request, pk):
    try:
        services.delete_draft(request.user, pk, request.POST.get("revision"))
    except Conflict:
        messages.error(request, "Удалить можно только неопубликованный черновик, который не на проверке.")
        return redirect("tournaments:step", pk=pk, step=5)
    messages.success(request, "Черновик удалён.")
    return redirect("tournaments:mine")


@login_required
@require_POST
def cancel(request, pk):
    try:
        services.cancel(request.user, pk, request.POST.get("revision"), request.POST.get("reason"))
    except Conflict:
        messages.error(request, CONFLICT_MSG)
    except ValueError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, "Соревнование отменено. В календаре оно отмечено как отменённое.")
    return redirect("tournaments:step", pk=pk, step=5)


@login_required
@require_GET
def regulation_working(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    if not services.can_view_internal(request.user, t):
        raise PermissionDenied
    if not t.regulation:
        raise Http404
    return _file_response(t.regulation.name, f"polozhenie-{t.pk}-chernovik.pdf")


def _file_response(name, filename):
    if not name or not default_storage.exists(name):
        raise Http404
    resp = FileResponse(default_storage.open(name, "rb"), content_type="application/pdf", as_attachment=False, filename=filename)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "sandbox"
    return resp


# ---------------- команда ----------------

@login_required
@require_http_methods(["GET", "POST"])
def team(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    if not services.can_view_internal(request.user, t):
        raise PermissionDenied
    manage = services.can_manage(request.user, t)
    form = InviteForm(request.POST or None)
    if request.method == "POST":
        if not manage:
            raise PermissionDenied
        if form.is_valid():
            phone, role = form.cleaned_data["phone"], form.cleaned_data["role"]
            if phone == t.owner.phone and role == TeamMember.Role.ORGANIZER:
                form.add_error("phone", "Это создатель турнира — у него уже полное управление.")
            else:
                try:
                    with transaction.atomic():
                        m = TeamMember.objects.create(tournament=t, phone=phone, role=role, invited_by=request.user)
                        audit(request.user, "team.invite", t, phone=phone, role=role, member=m.pk)
                except IntegrityError:
                    form.add_error("phone", "Этому номеру уже выдана эта роль или отправлено приглашение.")
                else:
                    messages.success(request, "Приглашение отправлено. Роль заработает, когда человек примет его в своём кабинете.")
                    return redirect("tournaments:team", pk=t.pk)
    members = t.team.select_related("user").exclude(state__in=[TeamMember.State.DECLINED, TeamMember.State.REVOKED])
    return render(request, "tournaments/team.html", {"t": t, "members": members, "form": form, "manage": manage})


@login_required
@require_POST
def team_revoke(request, pk, member_id):
    t = get_object_or_404(Tournament, pk=pk)
    services.require_manage(request.user, t)
    with transaction.atomic():
        m = get_object_or_404(TeamMember.objects.select_for_update(), pk=member_id, tournament=t)
        if m.state in (TeamMember.State.PENDING, TeamMember.State.ACCEPTED):
            m.state = TeamMember.State.REVOKED
            m.responded_at = timezone.now()
            m.save()
            audit(request.user, "team.revoke", t, member=m.pk, phone=m.phone, role=m.role)
    messages.success(request, "Доступ отозван.")
    return redirect("tournaments:team", pk=t.pk)


@login_required
def invitations(request):
    pending = TeamMember.objects.filter(phone=request.user.phone, state=TeamMember.State.PENDING).select_related("tournament", "invited_by")
    return render(request, "tournaments/invitations.html", {"pending": pending})


@login_required
@require_POST
def invitation_respond(request, member_id):
    with transaction.atomic():
        m = get_object_or_404(TeamMember.objects.select_for_update(), pk=member_id, state=TeamMember.State.PENDING)
        if m.phone != request.user.phone:
            raise PermissionDenied
        accept = request.POST.get("decision") == "accept"
        m.state = TeamMember.State.ACCEPTED if accept else TeamMember.State.DECLINED
        m.user = request.user if accept else None
        m.responded_at = timezone.now()
        m.save()
        audit(request.user, "team.accept" if accept else "team.decline", m.tournament, member=m.pk, role=m.role)
    messages.success(request, "Приглашение принято." if accept else "Приглашение отклонено.")
    return redirect("tournaments:invitations")


# ---------------- публичная часть ----------------

PUBLIC_STATES = [Tournament.Publication.PUBLISHED, Tournament.Publication.CANCELLED, Tournament.Publication.FINISHED]


def calendar(request):
    q = request.GET.get("q", "").strip()
    discipline = request.GET.get("discipline", "")
    region = request.GET.get("region", "")
    month = request.GET.get("month", "")
    only_open = request.GET.get("open") == "1"
    past = request.GET.get("past") == "1"

    qs = Tournament.objects.filter(publication__in=PUBLIC_STATES)
    today = timezone.localdate()
    if past:
        qs = qs.filter(pub_ends_on__lt=today).order_by("-pub_starts_on")
    else:
        qs = qs.filter(pub_ends_on__gte=today - datetime.timedelta(days=1)).order_by("pub_starts_on")
    if q:
        qs = qs.filter(pub_title__icontains=q)
    if discipline:
        qs = qs.filter(pub_disciplines__contains=[discipline])
    if region:
        qs = qs.filter(pub_region=region)
    month_label = ""
    if month:
        try:
            y, m = (int(x) for x in month.split("-"))
            first = datetime.date(y, m, 1)
            nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
            qs = qs.filter(pub_starts_on__lt=nxt, pub_ends_on__gte=first)
            month_label = _month_name(first)
        except (ValueError, TypeError):
            month = ""
    items = list(qs[:200])
    if only_open:
        items = [t for t in items if t.public_entries_open()]
    cards = [_card(t) for t in items]
    active = []
    if q:
        active.append(f"«{q}»")
    if month_label:
        active.append(month_label)
    if discipline:
        active.append(dict(DISCIPLINE_CHOICES).get(discipline, ""))
    if region:
        active.append(REGION_NAMES.get(region, ""))
    if only_open:
        active.append("Приём открыт")
    if past:
        active.append("Прошедшие")
    months = _month_options(today)
    return render(
        request,
        "core/calendar.html",
        {
            "cards": cards, "q": q, "discipline": discipline, "region": region, "month": month,
            "only_open": only_open, "past": past, "active": active, "months": months,
            "disciplines": DISCIPLINE_CHOICES, "regions": REGION_CHOICES,
            "filters_open": bool(discipline or region or month or only_open or past),
        },
    )


MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_SHORT = ["ЯНВ", "ФЕВ", "МАР", "АПР", "МАЙ", "ИЮН", "ИЮЛ", "АВГ", "СЕН", "ОКТ", "НОЯ", "ДЕК"]
WEEKDAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


def _month_name(d):
    return f"{MONTHS[d.month - 1].capitalize()} {d.year}"


def _month_options(today):
    out, d = [], today.replace(day=1)
    for _ in range(12):
        out.append((d.strftime("%Y-%m"), _month_name(d)))
        d = datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def _card(t):
    s = t.snapshot or {}
    start, end = t.pub_starts_on, t.pub_ends_on
    if start == end:
        days = f"{start.day}"
        wd = WEEKDAYS[start.weekday()]
    else:
        days = f"{start.day}–{end.day}" if start.month == end.month else f"{start.day}.{start.month:02d}–{end.day}.{end.month:02d}"
        wd = f"{WEEKDAYS[start.weekday()]}–{WEEKDAYS[end.weekday()]}"
    disc = s.get("disciplines", [])
    return {
        "t": t,
        "month": MONTHS_SHORT[start.month - 1],
        "days": days,
        "weekdays": wd,
        "venue": s.get("venue") or {},
        "tz_label": s.get("timezone_label", ""),
        "disciplines": [(d, dict(DISCIPLINE_CHOICES).get(d, d), DISCIPLINE_COLORS.get(d, "")) for d in disc],
        "programs_count": len(s.get("programs", [])),
        "open": t.public_entries_open(),
        "color": DISCIPLINE_COLORS.get(disc[0], "lemon") if disc else "lemon",
    }


def public_detail(request, pk):
    t = get_object_or_404(Tournament, pk=pk, publication__in=PUBLIC_STATES)
    s = t.snapshot
    days = {}
    for p in s["programs"]:
        days.setdefault(p["day"], []).append(p)
    day_list = [
        {"date": datetime.date.fromisoformat(d), "weekday": ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][datetime.date.fromisoformat(d).weekday()], "programs": progs}
        for d, progs in sorted(days.items())
    ]
    deadline = datetime.date.fromisoformat(s["entry_deadline"]) if s.get("entry_deadline") else None
    days_left = None
    if deadline and t.pub_timezone:
        local_today = timezone.now().astimezone(zoneinfo.ZoneInfo(t.pub_timezone)).date()
        days_left = (deadline - local_today).days
    return render(
        request,
        "tournaments/public_detail.html",
        {
            "t": t, "s": s, "days": day_list, "deadline": deadline, "days_left": days_left,
            "open": t.public_entries_open(), "card": _card(t),
            "can_manage": request.user.is_authenticated and services.can_manage(request.user, t),
            "can_judge": request.user.is_authenticated and bool(services.roles_of(request.user, t) & {"organizer", "judge"}),
            "can_stable": request.user.is_authenticated and bool(services.roles_of(request.user, t) & {"organizer", "stable"}),
            "has_stables": t.stable_categories.filter(is_active=True).exists() or t.services.filter(is_active=True).exists(),
            **_public_tabs(request, t),
        },
    )


def _public_tabs(request, t):
    """Вкладки «Стартовые» и «Результаты»: только опубликованные версии (ТЗ, разделы 10–11)."""
    from apps.conduct.models import ResultPublication, StartListPublication
    from apps.entries.models import Entry

    tab = request.GET.get("tab", "")
    if tab not in ("starts", "results"):
        tab = ""
    starts, results = [], []
    if tab == "starts":
        pubs = {}
        for pub in StartListPublication.objects.filter(program__tournament=t).select_related("program").order_by("program_id", "-version"):
            pubs.setdefault(pub.program_id, pub)
        ids = [r["entry"] for pub in pubs.values() for r in pub.rows]
        inactive = {e.pk for e in Entry.objects.filter(pk__in=ids) if not e.is_active}
        for p in t.programs.all():
            pub = pubs.get(p.pk)
            if pub:
                rows = [dict(r, mark={"withdrawn": "Снят", "no_show": "Не явился"}.get(r.get("status")) or ("Снят" if r["entry"] in inactive else "")) for r in pub.rows]
                starts.append({"p": p, "pub": pub, "rows": rows})
    elif tab == "results":
        pubs = {}
        for pub in ResultPublication.objects.filter(tclass__program__tournament=t).select_related("tclass__program").order_by("tclass_id", "-version"):
            pubs.setdefault(pub.tclass_id, pub)
        for p in t.programs.prefetch_related("classes"):
            for c in p.classes.all():
                pub = pubs.get(c.pk)
                if pub:
                    keys = p.result_fields
                    rows = [dict(r, cells=[r["values"].get(k) for k in keys]) for r in pub.rows]
                    results.append({"p": p, "c": c, "pub": pub, "rows": rows})
    return {"tab": tab, "starts": starts, "results": results}


def public_regulation(request, pk):
    t = get_object_or_404(Tournament, pk=pk, publication__in=PUBLIC_STATES)
    return _file_response(t.published_regulation, f"polozhenie-{t.pk}.pdf")
