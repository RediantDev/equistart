from django.contrib.auth.decorators import login_required
from django.db import connection
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache


def privacy(request):
    return render(request, "legal/privacy.html", {"doc": "privacy"})


def consent(request):
    return render(request, "legal/consent.html", {"doc": "consent"})


def publication_consent(request):
    return render(request, "legal/publication_consent.html", {"doc": "publication"})


@never_cache
def health_live(request):
    return JsonResponse({"status": "ok"})


@never_cache
def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@login_required
def notifications(request):
    show = request.GET.get("show", "unread")
    qs = Notification.objects.filter(user=request.user)
    if show == "unread":
        qs = qs.filter(read_at__isnull=True)
    return render(request, "core/notifications.html", {"items": qs[:200], "show": show})


@login_required
@require_POST
def notification_open(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if not n.read_at:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    url = n.url if n.url and url_has_allowed_host_and_scheme(n.url, allowed_hosts={request.get_host()}) else "/notifications/"
    return redirect(url)


@login_required
@require_POST
def notifications_read_all(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("core:notifications")
