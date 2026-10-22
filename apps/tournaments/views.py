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
    (2, "Программа и зачёты"),
    (3, "Положение и условия"),
    (4, "Проверка и отправка"),
]
LAST = len(STEPS)
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
        return redirect("tournaments:step", pk=t.pk, step=LAST)
    return None


def _step_state(t):
    """Номер первого незаполненного шага и признаки готовности шагов."""
    probs = services.problems(t)
    bad = {p.step for p in probs}
    return probs, bad


def _wizard_ctx(t, step, **extra):
    probs, bad = _step_state(t)
    first_bad = min(bad) if bad else LAST + 1
    steps = [
        {"n": n, "name": name, "done": n not in bad and n < LAST, "current": n == step, "reachable": n <= first_bad or n <= step}
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
    initial = {"contact_name": last.contact_name, "contact": last.contact, "venue": last.venue_id, "level": last.level} if last else {}
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
    t.contact_name = c.get("contact_name", "").strip()
    t.contact = c.get("contact", "").strip()
    t.level = c.get("level") or Tournament.Level.CLUB
    t.classification = bool(c.get("classification"))


# ---------------- шаги мастера ----------------

@login_required
@require_http_methods(["GET", "POST"])
def step(request, pk, step):
    if step not in range(1, LAST + 1):
        raise Http404
    t = _managed(request, pk)
    if request.method == "GET" and step != LAST and not t.is_locked:
        probs, bad = _step_state(t)
        first_bad = min(bad) if bad else LAST + 1
        if step > first_bad:
            messages.error(request, "Сначала заполните предыдущий шаг.")
            return redirect("tournaments:step", pk=t.pk, step=first_bad)
    if step == 1:
        return _step1(request, t)
    if step == 2:
        return render(request, "tournaments/step2.html", _wizard_ctx(t, 2, **_step2_ctx(t)))
    if step == 3:
        return _step3(request, t)
    return _step4(request, t)


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
                "venue": t.venue_id, "contact_name": t.contact_name, "contact": t.contact,
                "level": t.level, "classification": t.classification,
            },
        )
    return render(request, "tournaments/step1.html", _wizard_ctx(t, 1, form=form))


def _step2_ctx(t, form=None, editing=None, class_form=None, class_program=None, editing_class=None, user=None):
    from apps.sport.jumping import ROUTE_TYPES
    from apps.sport.models import ClassTemplate

    days = t.days()
    programs = list(t.programs.all().prefetch_related("classes").select_related("dressage_test"))
    for p in programs:
        if class_program is not None and p.pk == class_program.pk:
            p.form, p.editing_class, p.form_open = class_form, editing_class, True
        else:
            p.form, p.editing_class, p.form_open = ClassForm(p), None, False
    by_day = [{"day": d, "programs": [p for p in programs if p.day == d]} for d in days]
    orphans = [p for p in programs if p.day not in days]
    return {
        "by_day": by_day,
        "orphans": orphans,
        "form": form or ProgramForm(t, user, initial={"day": days[0].isoformat(), "discipline": "jumping"} if days else None),
        "editing": editing,
        "templates": ClassTemplate.objects.filter(is_active=True),
        "route_types": ROUTE_TYPES,
        "discipline_colors": DISCIPLINE_COLORS,
    }


@login_required
@require_POST
def program_save(request, pk, item_id=None):
    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    instance = get_object_or_404(ProgramItem, pk=item_id, tournament=t) if item_id else None
    form = ProgramForm(t, request.user, request.POST, instance=instance)
    if not form.is_valid():
        return render(request, "tournaments/step2.html", _wizard_ctx(t, 2, **_step2_ctx(t, form=form, editing=instance, user=request.user)), status=422)
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
    messages.success(request, f"«{obj.name}» сохранено.")
    return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + f"#program-{obj.pk}")


@login_required
@require_GET
def program_edit(request, pk, item_id):
    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    item = get_object_or_404(ProgramItem, pk=item_id, tournament=t)
    form = ProgramForm(t, request.user, instance=item, initial={"day": item.day.isoformat()})
    return render(request, "tournaments/step2.html", _wizard_ctx(t, 2, **_step2_ctx(t, form=form, editing=item, user=request.user)))


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
        messages.error(request, "Уже поданы заявки — удалить нельзя. Отмените заявки или измените маршрут.")
        return redirect("tournaments:step", pk=pk, step=2)
    messages.success(request, f"«{name}» удалено из программы вместе с зачётами.")
    return redirect("tournaments:step", pk=pk, step=2)


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
            request, "tournaments/step2.html",
            _wizard_ctx(t, 2, **_step2_ctx(t, class_form=form, class_program=program, editing_class=instance, user=request.user)), status=422,
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
        return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + "#programs")
    messages.success(request, f"Зачёт «{obj.name}» сохранён.")
    return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + f"#program-{program.pk}")


@login_required
@require_GET
def class_edit(request, pk, program_id, class_id):
    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    program = get_object_or_404(ProgramItem, pk=program_id, tournament=t)
    item = get_object_or_404(TournamentClass, pk=class_id, program=program)
    form = ClassForm(program, instance=item)
    return render(request, "tournaments/step2.html", _wizard_ctx(t, 2, **_step2_ctx(t, class_form=form, class_program=program, editing_class=item, user=request.user)))


@login_required
@require_POST
def class_from_template(request, pk, program_id):
    """Зачёт из шаблона одним нажатием: годы рождения считаются от года турнира (TRN-04)."""
    from apps.sport.models import ClassTemplate

    t = _managed(request, pk)
    if (r := _editable_or_redirect(request, t)):
        return r
    program = get_object_or_404(ProgramItem, pk=program_id, tournament=t)
    tpl = get_object_or_404(ClassTemplate, pk=request.POST.get("template"), is_active=True)
    height = request.POST.get("height") or None
    if height is not None:
        try:
            height = int(height)
        except ValueError:
            height = None
        if height not in (program.heights or []):
            height = None
    try:
        with transaction.atomic():
            locked = _managed(request, pk, lock=True)
            services.check_revision(locked, request.POST.get("revision"))
            data = tpl.for_year((locked.starts_on or timezone.localdate()).year)
            obj = TournamentClass.objects.create(program=program, template=tpl, height=height, position=program.classes.count(), **data)
            locked.mark_changed()
            locked.save()
            audit(request.user, "class.save", locked, tclass=obj.pk, name=obj.name, template=tpl.pk)
    except Conflict:
        messages.error(request, CONFLICT_MSG)
        return redirect("tournaments:step", pk=pk, step=2)
    messages.success(request, f"Зачёт «{obj.label}» добавлен: {obj.conditions or 'без ограничений'}. Условия можно поправить.")
    return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + f"#program-{program.pk}")


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
        return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + "#programs")
    except ProtectedError:
        messages.error(request, "В этот зачёт уже поданы заявки — удалить его нельзя.")
        return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + "#programs")
    messages.success(request, f"Зачёт «{name}» удалён.")
    return redirect(reverse("tournaments:step", kwargs={"pk": pk, "step": 2}) + "#programs")


def _step3(request, t):
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
                    audit(request.user, "tournament.edit", locked, step=3, new_regulation=bool(new_file))
            except Conflict:
                messages.error(request, CONFLICT_MSG)
                return redirect("tournaments:step", pk=t.pk, step=3)
            messages.success(request, "Сохранено.")
            return redirect("tournaments:step", pk=t.pk, step=4 if "next" in request.POST else 3)
    else:
        form = TermsForm(instance=t)
    return render(request, "tournaments/step3.html", _wizard_ctx(t, 3, form=form))


def _step4(request, t):
    if request.method == "POST":
        try:
            services.submit(request.user, t.pk, request.POST.get("revision"))
        except Conflict:
            messages.error(request, CONFLICT_MSG)
        except ValueError:
            messages.error(request, "Не всё заполнено — исправьте пункты ниже.")
        else:
            messages.success(request, "Турнир отправлен на проверку. Мы сообщим о решении модератора в кабинете.")
        return redirect("tournaments:step", pk=t.pk, step=LAST)
    preview = services.build_snapshot(t) if t.starts_on else None
    history = AuditLog.objects.filter(target_type="tournament", target_id=t.pk).select_related("actor")[:20]
    return render(request, "tournaments/step4.html", _wizard_ctx(t, LAST, preview=preview, history=history, tz_label=tz_label(t.timezone) if t.timezone else ""))


@login_required
@require_POST
def delete(request, pk):
    try:
        services.delete_draft(request.user, pk, request.POST.get("revision"))
    except Conflict:
        messages.error(request, "Удалить можно только неопубликованный черновик, который не на проверке.")
        return redirect("tournaments:step", pk=pk, step=LAST)
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
    return redirect("tournaments:step", pk=pk, step=LAST)


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


def _num(v):
    if v is None or v == "":
        return ""
    return str(v).replace(".", ",")


def _mobile_summary(fields, cells):
    """Короткая строка результата для карточки на телефоне: главное видно без прокрутки вбок."""
    fields, cells = list(fields or []), list(cells or [])
    if "%" in fields and any(f.endswith(": %") for f in fields):  # выездка
        pct = _num(cells[fields.index("%")]) if fields.index("%") < len(cells) else ""
        judges = [f"{f.split(':')[0]} {_num(cells[i])}" for i, f in enumerate(fields) if f.endswith(": %") and i < len(cells) and cells[i] not in (None, "")]
        errs = cells[fields.index("Ошибки")] if "Ошибки" in fields and fields.index("Ошибки") < len(cells) else ""
        detail = " · ".join(judges) + (f" · ошибки в схеме: {errs}" if errs else "")
        return (f"{pct} %" if pct else ""), detail
    parts = []
    i = 0
    while i < len(fields):
        f = fields[i]
        if f.startswith("Ш/о") and i + 1 < len(fields) and fields[i + 1].startswith("Время"):
            pen, tm = (cells[i] if i < len(cells) else ""), (cells[i + 1] if i + 1 < len(cells) else "")
            name = f[3:].strip() or "маршрут"
            is_num = str(pen).replace(",", "").replace(".", "").isdigit()
            if pen not in (None, "") and not is_num and tm in (None, ""):
                parts.append(str(pen))  # «Искл.», «Сошёл» — без подписи гита
            elif pen not in (None, "") or tm not in (None, ""):
                parts.append(f"{name.capitalize()}: {_num(pen)}" + (f" / {_num(tm)} с" if tm not in (None, "") else ""))
            i += 2
            continue
        if i < len(cells) and cells[i] not in (None, ""):
            parts.append(f"{f}: {_num(cells[i])}")
        i += 1
    return (parts[0] if parts else ""), " · ".join(parts[1:])


def _public_tabs(request, t):
    """Вкладки «Стартовые» и «Результаты»: только опубликованные версии (ТЗ, разделы 10–11).
    Результаты — по маршрутам, внутри маршрута каждый зачёт отдельно, внутри зачёта — по высотам (JMP-09)."""
    from apps.conduct.models import ResultPublication, StartListPublication
    from apps.entries.models import Entry

    tab = request.GET.get("tab", "")
    if tab not in ("starts", "results"):
        tab = ""
    out = {"tab": tab, "starts": [], "results": [], "days_nav": [], "day": ""}
    if not tab:
        return out
    programs = list(t.programs.prefetch_related("classes").order_by("day", "start_time", "position", "id"))
    if tab == "starts":
        pubs = {}
        for pub in StartListPublication.objects.filter(program__tournament=t).order_by("program_id", "-version"):
            pubs.setdefault(pub.program_id, pub)
        have = [p for p in programs if p.pk in pubs]
    else:
        pubs = {}
        for pub in ResultPublication.objects.filter(tclass__program__tournament=t).order_by("tclass_id", "-version"):
            pubs.setdefault(pub.tclass_id, pub)
        have = [p for p in programs if any(c.pk in pubs for c in p.classes.all())]
    days = sorted({p.day for p in have})
    out["days_nav"] = days
    day = request.GET.get("day", "")
    if days and len(days) > 1:
        if day == "all":
            pass
        else:
            try:
                d = datetime.date.fromisoformat(day)
            except ValueError:
                d = None
            if d not in days:
                local_today = timezone.now().astimezone(zoneinfo.ZoneInfo(t.pub_timezone or "Europe/Moscow")).date()
                d = local_today if local_today in days else (days[-1] if tab == "results" else days[0])
            day = d.isoformat()
            have = [p for p in have if p.day == d]
    out["day"] = day
    if tab == "starts":
        ids = [r["entry"] for p in have for r in pubs[p.pk].rows]
        inactive = {e.pk for e in Entry.objects.filter(pk__in=ids) if not e.is_active}
        for p in have:
            pub = pubs[p.pk]
            rows = [dict(r, mark={"withdrawn": "Снят", "no_show": "Не явился"}.get(r.get("status")) or ("Снят" if r["entry"] in inactive else ""))
                    for r in pub.rows]
            out["starts"].append({"p": p, "pub": pub, "rows": rows, "timed": any(r.get("time") for r in rows)})
    else:
        need_h = {r["entry"] for p in have if len(p.heights or []) > 1 for c in p.classes.all() if c.pk in pubs
                  for r in pubs[c.pk].rows if "height" not in r}
        hmap = dict(Entry.objects.filter(pk__in=need_h).values_list("pk", "height")) if need_h else {}
        for p in have:
            blocks = []
            for c in p.classes.all():
                pub = pubs.get(c.pk)
                if not pub:
                    continue
                rows = [r if "cells" in r else dict(r, cells=[r["values"].get(k) for k in p.result_fields]) for r in pub.rows]
                rows = [dict(r, summary=_mobile_summary(pub.fields, r["cells"])) for r in rows]
                groups = []
                if len(p.heights or []) > 1:
                    by_h = {}
                    for r in rows:
                        by_h.setdefault(r.get("height") or hmap.get(r["entry"]), []).append(r)
                    for h in sorted(by_h, key=lambda x: x or 0):
                        groups.append({"height": h, "rows": sorted(by_h[h], key=lambda r: (r["place"] is None, r["place"] or 0))})
                else:
                    groups.append({"height": None, "rows": rows})
                blocks.append({"c": c, "pub": pub, "groups": groups})
            out["results"].append({"p": p, "classes": blocks})
    return out


def public_regulation(request, pk):
    t = get_object_or_404(Tournament, pk=pk, publication__in=PUBLIC_STATES)
    return _file_response(t.published_regulation, f"polozhenie-{t.pk}.pdf")
