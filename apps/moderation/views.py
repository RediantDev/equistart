"""Кабинет модератора и администратора (ТЗ 13.3, ADM-01…ADM-10)."""

import datetime
import shutil
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.models import Category, User
from apps.core import xlsx
from apps.core.models import ACTION_LABELS, AuditLog, OutgoingMail, SiteSettings, audit
from apps.tournaments.moderation import moderator_required
from apps.tournaments.regions import REGION_CHOICES, REGION_NAMES

from . import services
from .services import CabinetError


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _page(request, qs, per=50):
    return Paginator(qs, per).get_page(request.GET.get("page"))


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


# ---------------- пользователи (ADM-04) ----------------

USER_STATES = [("", "Все"), ("active", "Действующие"), ("blocked", "Заблокированные"), ("deleted", "Удалённые"), ("moderators", "Модераторы")]
CATEGORY_FIELD = {"athlete": "is_athlete", "coach": "is_coach", "organizer": "is_organizer", "judge": "is_judge", "club": "is_club"}


def _users_qs(request):
    q = (request.GET.get("q") or "").strip()
    qs = User.objects.all()
    if q:
        cond = Q()
        for part in q.split():
            cond &= Q(last_name__icontains=part) | Q(first_name__icontains=part) | Q(middle_name__icontains=part)
        digits = _digits(q)
        if request.user.is_admin and len(digits) >= 4:
            cond |= Q(phone__contains=digits)  # ADM-04: поиск по телефону — только администратору
        qs = qs.filter(cond)
    state = request.GET.get("state", "")
    if state == "active":
        qs = qs.filter(is_active=True)
    elif state == "blocked":
        qs = qs.filter(blocked_at__isnull=False, is_active=False, deleted_at__isnull=True)
    elif state == "deleted":
        qs = qs.filter(deleted_at__isnull=False)
    elif state == "moderators":
        qs = qs.filter(is_moderator=True)
    cat = request.GET.get("cat", "")
    if cat in CATEGORY_FIELD:
        qs = qs.filter(**{CATEGORY_FIELD[cat]: True})
    return qs.order_by("-date_joined")


@moderator_required
def users(request):
    page = _page(request, _users_qs(request))
    return render(request, "moderation/users.html", {
        "page": page, "q": request.GET.get("q", ""), "state": request.GET.get("state", ""), "cat": request.GET.get("cat", ""),
        "states": USER_STATES, "cats": Category.choices, "total": page.paginator.count,
    })


def _user_context(request, u):
    from apps.clubs.models import Club
    from apps.profiles.models import Athlete, Horse
    from apps.support.models import Ticket
    from apps.tournaments.models import TeamMember, Tournament

    ctx = {
        "u": u,
        "own_tournaments": Tournament.objects.filter(owner=u).order_by("-created_at")[:20],
        "team": TeamMember.objects.filter(user=u, state=TeamMember.State.ACCEPTED).select_related("tournament").order_by("-pk")[:20],
        "clubs": Club.objects.filter(manager=u, merged_into__isnull=True),
        "athletes": Athlete.objects.filter(Q(manager=u) | Q(linked_user=u), archived=False).distinct()[:30],
        "horses": Horse.objects.filter(manager=u, archived=False)[:30],
        "tickets": Ticket.objects.filter(author=u).order_by("-created_at")[:20],
        "can_block": u.pk != request.user.pk and not (u.is_moderator or u.is_superuser) and not u.deleted_at,
    }
    if request.user.is_admin:
        ctx["history"] = AuditLog.objects.filter(Q(target_type="user", target_id=u.pk) | Q(actor=u)).select_related("actor")[:40]
    return ctx


@moderator_required
@require_http_methods(["GET", "POST"])
def user_detail(request, pk):
    u = get_object_or_404(User, pk=pk)
    temp_password = None
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "block":
                services.block(request.user, u.pk, request.POST.get("reason", ""))
                messages.success(request, "Аккаунт заблокирован. Пользователь больше не может войти.")
            elif action == "unblock":
                services.unblock(request.user, u.pk)
                messages.success(request, "Аккаунт разблокирован.")
            elif action == "reset_password":
                temp_password = services.reset_password(request.user, u.pk)
            elif action == "delete":
                if request.POST.get("confirm") != "1":
                    raise CabinetError("Подтвердите удаление отметкой.")
                services.delete_account(request.user, u.pk)
                messages.success(request, "Аккаунт удалён. Войти в него больше нельзя; данные удалятся через 30 дней.")
            else:
                raise CabinetError("Выберите действие.")
        except CabinetError as e:
            messages.error(request, str(e))
        if temp_password is None:
            return redirect("moderation:user", pk=u.pk)
        u.refresh_from_db()
    ctx = _user_context(request, u)
    ctx["temp_password"] = temp_password
    return render(request, "moderation/user.html", ctx)


# ---------------- дубли (ADM-05) ----------------

@moderator_required
@require_http_methods(["GET", "POST"])
def duplicates(request):
    kind = request.GET.get("kind") or request.POST.get("kind") or "athlete"
    if kind not in ("athlete", "horse"):
        kind = "athlete"
    if request.method == "POST":
        try:
            if request.POST.get("confirm") != "1":
                raise CabinetError("Подтвердите объединение отметкой — отменить его нельзя.")
            main, report = services.merge(request.user, kind, request.POST.get("main"), request.POST.getlist("dup"))
            n = len(report)
            messages.success(request, f"Объединено записей: {n}. Выступления, заявки и документы перенесены к основной записи.")
        except CabinetError as e:
            messages.error(request, str(e))
        return redirect(f"{request.path}?kind={kind}")
    from apps.entries.models import Entry

    groups = services.athlete_duplicates() if kind == "athlete" else services.horse_duplicates()
    ids = [o.pk for g in groups for o in g]
    field = "athlete_id" if kind == "athlete" else "horse_id"
    starts = dict(Entry.objects.filter(**{f"{field}__in": ids}).values_list(field).annotate(n=Count("pk")))
    for g in groups:
        for o in g:
            o.starts = starts.get(o.pk, 0)
    return render(request, "moderation/duplicates.html", {
        "kind": kind, "groups": groups,
        "athlete_groups": len(services.athlete_duplicates()) if kind == "horse" else len(groups),
        "horse_groups": len(services.horse_duplicates()) if kind == "athlete" else len(groups),
    })


# ---------------- все турниры (ADM-06) ----------------

def _tournaments_qs(request):
    from apps.tournaments.models import Tournament

    qs = Tournament.objects.select_related("owner", "venue")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(pub_title__icontains=q) | Q(owner__last_name__icontains=q))
    if request.GET.get("status") in Tournament.Status.values:
        qs = qs.filter(status=request.GET["status"])
    if request.GET.get("pub") in Tournament.Publication.values:
        qs = qs.filter(publication=request.GET["pub"])
    region = request.GET.get("region", "")
    if region:
        qs = qs.filter(Q(pub_region=region) | Q(venue__region=region))
    for key, lookup in (("from", "starts_on__gte"), ("to", "starts_on__lte")):
        try:
            qs = qs.filter(**{lookup: datetime.date.fromisoformat(request.GET.get(key, ""))})
        except ValueError:
            pass
    return qs.order_by("-starts_on", "-created_at")


@moderator_required
def tournaments(request):
    from apps.tournaments.models import Tournament

    page = _page(request, _tournaments_qs(request))
    return render(request, "moderation/tournaments.html", {
        "page": page, "total": page.paginator.count, "f": request.GET,
        "statuses": Tournament.Status.choices, "pubs": Tournament.Publication.choices, "regions": REGION_CHOICES,
        "region_names": REGION_NAMES,
    })


# ---------------- журнал (ADM-07) ----------------

TARGETS = [("", "Все объекты"), ("tournament", "Турниры"), ("user", "Пользователи"), ("club", "Клубы"), ("ticket", "Обращения"),
           ("athlete", "Спортсмены"), ("horse", "Лошади"), ("application", "Заявки"), ("entry", "Выступления"), ("service", "Сервис")]


@admin_required
def journal(request):
    qs = AuditLog.objects.select_related("actor")
    q = (request.GET.get("actor") or "").strip()
    if q:
        cond = Q()
        for part in q.split():
            cond &= Q(actor__last_name__icontains=part) | Q(actor__first_name__icontains=part)
        qs = qs.filter(cond)
    if request.GET.get("target"):
        qs = qs.filter(target_type=request.GET["target"])
    if (request.GET.get("id") or "").isdigit():
        qs = qs.filter(target_id=int(request.GET["id"]))
    if request.GET.get("action"):
        qs = qs.filter(action__startswith=request.GET["action"])
    if request.GET.get("admin") == "1":
        qs = qs.filter(data__as_admin=True)
    for key, lookup in (("from", "created_at__date__gte"), ("to", "created_at__date__lte")):
        try:
            qs = qs.filter(**{lookup: datetime.date.fromisoformat(request.GET.get(key, ""))})
        except ValueError:
            pass
    page = _page(request, qs, 100)
    prefixes = sorted({a.split(".")[0] for a in ACTION_LABELS})
    return render(request, "moderation/journal.html", {"page": page, "f": request.GET, "targets": TARGETS, "prefixes": prefixes})


# ---------------- настройки (ADM-08) ----------------

@admin_required
@require_http_methods(["GET", "POST"])
def settings_view(request):
    from .forms import SiteSettingsForm

    obj = SiteSettings.get()
    form = SiteSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        before = {f: getattr(SiteSettings.get(), f) for f in form.changed_data}
        s = form.save(commit=False)
        s.updated_by = request.user
        s.save()
        audit(request.user, "settings.save", s, changed={f: [before.get(f), getattr(s, f)] for f in form.changed_data})
        messages.success(request, "Настройки сохранены.")
        return redirect("moderation:settings")
    return render(request, "moderation/settings.html", {"form": form, "obj": obj, "from_email": settings.DEFAULT_FROM_EMAIL,
                                                        "mail_configured": bool(getattr(settings, "EMAIL_HOST", ""))})


# ---------------- система (ADM-09) ----------------

def _disk():
    try:
        u = shutil.disk_usage(settings.MEDIA_ROOT if getattr(settings, "MEDIA_ROOT", "") else settings.BASE_DIR)
        return {"free_gb": round(u.free / 1024 ** 3, 1), "total_gb": round(u.total / 1024 ** 3, 1),
                "used_pct": round(100 * (u.total - u.free) / u.total) if u.total else 0}
    except OSError:
        return None


@admin_required
@require_http_methods(["GET", "POST"])
def system(request):
    from apps.core.mail import queue_mail
    from apps.tournaments.moderation import BACKUP_STALE_AFTER, _db_ok

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "test_mail":
            to = SiteSettings.get().moderator_email_list + ([request.user.email] if request.user.email else [])
            if not to:
                messages.error(request, "Некуда отправить: укажите email в настройках сервиса или в своём профиле.")
            else:
                queue_mail(to, "Проверка почты СтартЛист", "Это тестовое письмо из кабинета администратора. Если вы его получили — почта настроена.",
                           kind="system.test")
                messages.success(request, "Тестовое письмо поставлено в отправку: " + ", ".join(to) + ". Результат — в журнале писем ниже.")
        elif action == "retry_mail":
            from apps.core.mail import retry_pending

            ok, failed = retry_pending()
            messages.info(request, f"Повторная отправка: отправлено {ok}, ошибок {failed}.")
        return redirect("moderation:system")
    now = timezone.now()
    last_backup = AuditLog.objects.filter(action="service.backup").order_by("-created_at").first()
    errors = list(AuditLog.objects.filter(action="service.error", created_at__gte=now - datetime.timedelta(days=1))[:20])
    mails = OutgoingMail.objects.all()[:50]
    return render(request, "moderation/system.html", {
        "db_ok": _db_ok(), "last_backup": last_backup,
        "backup_ok": bool(last_backup and now - last_backup.created_at <= BACKUP_STALE_AFTER),
        "errors": errors, "errors_day": len(errors), "mails": mails,
        "mail_failed": OutgoingMail.objects.exclude(state=OutgoingMail.State.SENT).count(),
        "disk": _disk(), "mail_configured": bool(getattr(settings, "EMAIL_HOST", "")),
        "from_email": settings.DEFAULT_FROM_EMAIL, "env": settings.ENV,
    })


# ---------------- выгрузки Excel (ADM-10) ----------------

def _xlsx(data: bytes, name: str) -> HttpResponse:
    r = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r["Content-Disposition"] = f'attachment; filename="{name}"'
    return r


def _d(v):
    if not v:
        return ""
    if isinstance(v, datetime.datetime):
        return timezone.localtime(v).strftime("%d.%m.%Y %H:%M")
    return v.strftime("%d.%m.%Y")


@moderator_required
def export(request, kind):
    stamp = timezone.localdate().isoformat()
    if kind == "tournaments":
        rows = [["№", "Название", "Начало", "Окончание", "Регион", "Место", "Организатор", "Статус", "Публикация", "Создан"]]
        for t in _tournaments_qs(request)[:5000]:
            rows.append([t.pk, t.pub_title or t.title, _d(t.pub_starts_on or t.starts_on), _d(t.pub_ends_on or t.ends_on),
                         REGION_NAMES.get(t.pub_region or (t.venue.region if t.venue else ""), ""), t.venue.name if t.venue else "",
                         t.owner.full_name, t.get_status_display(), t.get_publication_display(), _d(t.created_at)])
        audit(request.user, "export.tournaments", request.user, rows=len(rows) - 1)
        return _xlsx(xlsx.build("Турниры", rows), f"tournaments-{stamp}.xlsx")
    if not request.user.is_admin:
        raise PermissionDenied
    if kind == "users":
        rows = [["№", "ФИО", "Телефон", "Email", "Роли", "Телефон подтверждён", "Зарегистрирован", "Последний вход", "Состояние"]]
        for u in _users_qs(request)[:20000]:
            state = "удалён" if u.deleted_at else ("заблокирован" if u.is_blocked else "действует")
            rows.append([u.pk, u.full_name, u.phone_display, u.email, ", ".join(u.category_labels()), "да" if u.phone_verified else "нет",
                         _d(u.date_joined), _d(u.last_login), state])
        audit(request.user, "export.users", request.user, rows=len(rows) - 1)
        return _xlsx(xlsx.build("Пользователи", rows), f"users-{stamp}.xlsx")
    if kind == "tickets":
        from apps.support.models import Ticket

        rows = [["№", "Создано", "Тема", "Статус", "Срочное", "Автор", "Турнир", "Последний ответ поддержки"]]
        for t in Ticket.objects.select_related("author", "tournament").order_by("-created_at")[:20000]:
            rows.append([t.pk, _d(t.created_at), t.get_topic_display(), t.get_status_display(), "да" if t.urgent else "",
                         t.contact_name or "гость", (t.tournament.pub_title or t.tournament.title) if t.tournament_id else "", _d(t.last_staff_at)])
        audit(request.user, "export.tickets", request.user, rows=len(rows) - 1)
        return _xlsx(xlsx.build("Обращения", rows), f"tickets-{stamp}.xlsx")
    raise PermissionDenied
