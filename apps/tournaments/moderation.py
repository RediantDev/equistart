"""Рабочее место модератора: проверка турниров перед публикацией."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
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
