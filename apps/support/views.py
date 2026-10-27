"""Поддержка пользователей (ТЗ 13.2): форма «Помощь», мои обращения, переписка, очередь модератора, частые вопросы."""

import mimetypes
import re
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.throttle import client_ip
from apps.core.models import AuditLog
from apps.tournaments.moderation import moderator_required

from . import services
from .forms import TicketForm
from .models import Faq, Ticket, TicketAttachment, Topic


def _safe_page(raw: str) -> str:
    """Страница, с которой открыта форма: только путь этого сайта."""
    if not raw:
        return ""
    u = urlparse(raw)
    if u.scheme or u.netloc or not raw.startswith("/") or raw.startswith("//"):
        return ""
    return raw[:300]


TOURNAMENT_IN_PATH = re.compile(r"^/(?:organizer/)?t/(\d+)/")


@require_http_methods(["GET", "POST"])
def new(request):
    user = request.user
    live = services.live_tournaments_for(user)
    page = _safe_page(request.POST.get("page") or request.GET.get("from", ""))
    ua = request.META.get("HTTP_USER_AGENT", "")[:300]
    initial = {"page": page}
    if request.GET.get("topic") in Topic.values:
        initial["topic"] = request.GET["topic"]
    m = TOURNAMENT_IN_PATH.match(page)
    if m:
        initial["tournament"] = int(m.group(1))
    if request.method == "POST":
        form = TicketForm(user, request.POST, request.FILES, live=live)
        if form.is_valid():
            try:
                key = services.check_rate(user, client_ip(request))
                data = dict(form.cleaned_data, page=page, user_agent=ua)
                t = services.create(user, data, form.cleaned_data.get("attachments") or [], rate_key=key)
            except services.SupportError as e:
                form.add_error(None, str(e))
            else:
                messages.success(request, f"Обращение №{t.pk} отправлено. Ответим в течение {services.ANSWER_WITHIN_TEXT}"
                                          + (" — на сайте и на email." if t.reply_email else " — уведомлением на сайте."))
                return redirect(t.get_absolute_url())
    else:
        form = TicketForm(user, initial=initial, live=live)
    return render(request, "support/new.html", {
        "form": form, "page": page, "ua": ua, "live": live, "within": services.ANSWER_WITHIN_TEXT,
    })


@login_required
def my_list(request):
    tickets = Ticket.objects.filter(author=request.user).order_by("-updated_at")
    return render(request, "support/my_list.html", {"tickets": tickets, "within": services.ANSWER_WITHIN_TEXT})


def _detail(request, t, token=""):
    if request.method == "POST":
        try:
            services.user_reply(request.user, t.pk, request.POST.get("text"), token=token)
            messages.success(request, "Ответ отправлен поддержке.")
            return redirect(request.path)
        except services.SupportError as e:
            messages.error(request, str(e))
    return render(request, "support/detail.html", {
        "t": t, "msgs": t.messages.select_related("author"), "files": t.attachments.all(), "token": token,
        "within": services.ANSWER_WITHIN_TEXT,
    })


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    t = get_object_or_404(Ticket, pk=pk)
    if t.author_id != request.user.pk:
        if request.user.is_moderator:
            return redirect("moderation:support_detail", t.pk)
        raise Http404
    return _detail(request, t)


@require_http_methods(["GET", "POST"])
def guest(request, token):
    t = get_object_or_404(Ticket, token=token)
    return _detail(request, t, token=token)


def attachment(request, pk):
    a = get_object_or_404(TicketAttachment.objects.select_related("ticket"), pk=pk)
    if not services.can_view(request.user, a.ticket, request.GET.get("k", "")) or not default_storage.exists(a.file.name):
        raise Http404
    ctype = mimetypes.guess_type(a.file.name)[0] or "application/octet-stream"
    if ctype not in ("image/jpeg", "image/png"):
        ctype = "application/octet-stream"
    resp = FileResponse(default_storage.open(a.file.name, "rb"), content_type=ctype)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, no-store"
    return resp


def faq(request):
    items = Faq.objects.filter(published=True)
    sections = []
    for code, label in Faq.Section.choices:
        rows = [i for i in items if i.section == code]
        if rows:
            sections.append((code, label, rows))
    from django.conf import settings

    return render(request, "support/faq.html", {"sections": sections, "telegram": settings.SUPPORT_TELEGRAM})


# ---------------- модератор (SUP-08) ----------------

@moderator_required
def mod_queue(request):
    qs = Ticket.objects.select_related("author", "tournament")
    f = {k: request.GET.get(k, "") for k in ("topic", "status", "urgent", "q")}
    if f["status"] == "open" or not f["status"]:
        qs = qs.exclude(status=Ticket.Status.RESOLVED)
        f["status"] = f["status"] or "open"
    elif f["status"] in Ticket.Status.values:
        qs = qs.filter(status=f["status"])
    if f["topic"] in Topic.values:
        qs = qs.filter(topic=f["topic"])
    if f["urgent"] == "1":
        qs = qs.filter(urgent=True)
    if f["q"]:
        q = f["q"].lstrip("№#")
        qs = qs.filter(Q(pk=int(q)) if q.isdigit() else Q(text__icontains=q) | Q(name__icontains=q))
    now = timezone.now()
    tickets = list(qs.order_by("-urgent", "created_at")[:200])
    for t in tickets:
        t.overdue = services.is_overdue(t, now)
    return render(request, "support/mod_queue.html", {
        "tickets": tickets, "f": f, "topics": Topic.choices, "statuses": Ticket.Status.choices,
        "overdue": sum(1 for t in tickets if t.overdue),
    })


@moderator_required
@require_http_methods(["GET", "POST"])
def mod_detail(request, pk):
    t = get_object_or_404(Ticket.objects.select_related("author", "tournament", "athlete", "horse", "club"), pk=pk)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "transfer":
                n = services.transfer_to_judges(request.user, t.pk)
                messages.success(request, f"Обращение передано команде турнира: уведомлений — {n}.")
            else:
                services.staff_reply(request.user, t.pk, request.POST.get("text"), request.POST.get("status"))
                messages.success(request, "Сохранено. Пользователь получит уведомление.")
            return redirect("moderation:support_detail", t.pk)
        except services.SupportError as e:
            messages.error(request, str(e))
    history = AuditLog.objects.filter(target_type="ticket", target_id=t.pk).select_related("actor")
    return render(request, "support/mod_detail.html", {
        "t": t, "msgs": t.messages.select_related("author"), "files": t.attachments.all(), "history": history,
        "statuses": Ticket.Status.choices, "overdue": services.is_overdue(t),
    })
