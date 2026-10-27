"""Рабочее место модератора: проверка турниров перед публикацией."""

from functools import wraps

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import AuditLog

from . import services
from .models import Tournament
from .regions import tz_label
from .services import Conflict


def moderator_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_moderator:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


# Срок ответа модератора — 1 рабочий день (SUP-06); всё, что ждёт дольше суток, подсвечивается.
OVERDUE_AFTER = datetime.timedelta(days=1)
# Копия делается раз в сутки (deploy/backup.sh); если последней нет дольше 26 часов — тревога.
BACKUP_STALE_AFTER = datetime.timedelta(hours=26)


def _db_ok() -> bool:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


@moderator_required
def panel(request):
    """Пульт администратора: что ждёт решения, состояние сервиса, цифры (решение 26.10.2026)."""
    from apps.accounts.models import User
    from apps.clubs.models import Club
    from apps.entries.models import Application, Entry

    now = timezone.now()
    week_ago = now - datetime.timedelta(days=7)
    day_ago = now - datetime.timedelta(days=1)

    tournaments = list(
        Tournament.objects.filter(status=Tournament.Status.REVIEW).select_related("owner", "venue").order_by("submitted_at")
    )
    clubs = list(
        Club.objects.filter(status=Club.Status.REVIEW, merged_into__isnull=True).select_related("manager").order_by("submitted_at")
    )
    for obj in tournaments + clubs:
        obj.overdue = bool(obj.submitted_at and now - obj.submitted_at > OVERDUE_AFTER)

    last_backup = AuditLog.objects.filter(action="service.backup").order_by("-created_at").first()
    backup_ok = bool(last_backup and now - last_backup.created_at <= BACKUP_STALE_AFTER)
    errors_day = AuditLog.objects.filter(action="service.error", created_at__gte=day_ago).count()

    stats = {
        "users": User.objects.filter(is_active=True).count(),
        "users_week": User.objects.filter(is_active=True, date_joined__gte=week_ago).count(),
        "tournaments_live": Tournament.objects.filter(
            publication=Tournament.Publication.PUBLISHED, pub_ends_on__gte=timezone.localdate()
        ).count(),
        "tournaments_week": Tournament.objects.filter(created_at__gte=week_ago).count(),
        "applications_week": Application.objects.filter(created_at__gte=week_ago).count(),
        "entries_week": Entry.objects.filter(created_at__gte=week_ago).count(),
        "clubs": Club.objects.filter(status=Club.Status.CONFIRMED, merged_into__isnull=True).count(),
    }
    from apps.support import services as support
    from apps.support.models import Ticket

    tickets = list(Ticket.objects.exclude(status__in=[Ticket.Status.RESOLVED, Ticket.Status.WAITING])
                   .select_related("author", "tournament").order_by("-urgent", "created_at")[:50])
    for tk in tickets:
        tk.overdue = support.is_overdue(tk, now)
    return render(
        request,
        "moderation/panel.html",
        {
            "tournaments": tournaments,
            "clubs": clubs,
            "tickets": tickets,
            "waiting_total": len(tournaments) + len(clubs) + len(tickets),
            "overdue_total": sum(1 for o in tournaments + clubs + tickets if o.overdue),
            "db_ok": _db_ok(),
            "last_backup": last_backup,
            "backup_ok": backup_ok,
            "errors_day": errors_day,
            "stats": stats,
        },
    )


@moderator_required
def queue(request):
    waiting = Tournament.objects.filter(status=Tournament.Status.REVIEW).select_related("owner", "venue").order_by("submitted_at")
    recent = (
        Tournament.objects.filter(reviewed_by__isnull=False)
        .exclude(status=Tournament.Status.REVIEW)
        .select_related("owner", "venue", "reviewed_by")
        .order_by("-reviewed_at")[:20]
    )
    return render(request, "moderation/queue.html", {"waiting": waiting, "recent": recent})


@moderator_required
def detail(request, pk):
    t = get_object_or_404(Tournament.objects.select_related("owner", "venue"), pk=pk)
    preview = services.build_snapshot(t) if t.starts_on else None
    history = AuditLog.objects.filter(target_type="tournament", target_id=t.pk).select_related("actor")[:30]
    return render(
        request,
        "moderation/detail.html",
        {"t": t, "preview": preview, "problems": services.problems(t), "history": history, "tz_label": tz_label(t.timezone) if t.timezone else ""},
    )


@moderator_required
@require_POST
def decide(request, pk):
    decision = request.POST.get("decision")
    try:
        if decision == "approve":
            services.approve(request.user, pk, request.POST.get("revision"), confirm_venue=request.POST.get("confirm_venue") == "1")
            messages.success(request, "Турнир опубликован. Он появился в календаре.")
        elif decision == "reject":
            services.reject(request.user, pk, request.POST.get("revision"), request.POST.get("comment"))
            messages.success(request, "Соревнование отклонено. Организатор получил уведомление с причиной.")
        elif decision == "return":
            services.return_for_fixes(request.user, pk, request.POST.get("revision"), request.POST.get("comment"))
            messages.success(request, "Турнир возвращён организатору с замечаниями.")
        else:
            messages.error(request, "Выберите решение.")
    except Conflict:
        messages.error(request, "Турнир уже изменился или решение по нему принято. Страница обновлена.")
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("moderation:detail", pk=pk)
    return redirect("moderation:queue")
