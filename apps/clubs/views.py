"""Клубы: публичный список и страница, кабинет клуба, новости, файлы, модерация."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.models import audit
from apps.tournaments.models import Tournament
from apps.tournaments.moderation import moderator_required
from apps.tournaments.regions import REGION_CHOICES

from . import services
from .forms import ClubForm, NewsForm
from .models import Club, ClubMember, ClubNews, ClubPhoto, NewsPhoto

PUBLIC_T = [Tournament.Publication.PUBLISHED, Tournament.Publication.FINISHED, Tournament.Publication.CANCELLED]


# ---------------- публичная часть ----------------

def club_list(request):
    q = request.GET.get("q", "").strip()
    region = request.GET.get("region", "")
    qs = Club.objects.filter(confirmed_at__isnull=False, merged_into__isnull=True)
    if q:
        qs = qs.filter(Q(pub_name__icontains=q) | Q(pub_settlement__icontains=q))
    if region:
        qs = qs.filter(pub_region=region)
    return render(request, "clubs/list.html", {"clubs": qs.order_by("pub_name"), "q": q, "region": region, "regions": REGION_CHOICES})


def detail(request, pk):
    club = get_object_or_404(Club, pk=pk)
    if club.merged_into_id:
        return redirect("clubs:detail", club.merged_into_id, permanent=True)
    if not services.can_view(request.user, club):
        raise Http404("Клуб ещё не подтверждён модератором.")
    today = timezone.localdate()
    ts = Tournament.objects.filter(venue_id=club.venue_id, publication__in=PUBLIC_T) if club.venue_id else Tournament.objects.none()
    upcoming = list(ts.filter(pub_ends_on__gte=today).order_by("pub_starts_on"))
    past = list(ts.filter(pub_ends_on__lt=today).order_by("-pub_starts_on")[:20])
    news = club.news.filter(hidden=False).prefetch_related("photos").select_related("tournament")[:20]
    return render(request, "clubs/detail.html", {"club": club, "upcoming": upcoming, "past": past, "news": news,
                                                 "manage": services.can_manage(request.user, club)})


def file(request, kind, pk):
    """Логотип и фото клуба: публично — только у подтверждённого клуба (файлы не раздаются напрямую)."""
    if kind == "logo":
        club = get_object_or_404(Club, pk=pk)
        f = club.logo
    elif kind == "photo":
        obj = get_object_or_404(ClubPhoto.objects.select_related("club"), pk=pk)
        club, f = obj.club, obj.file
    elif kind == "news":
        obj = get_object_or_404(NewsPhoto.objects.select_related("news__club"), pk=pk)
        club, f = obj.news.club, obj.file
        if obj.news.hidden and not (services.can_manage(request.user, club) or request.user.is_authenticated and request.user.is_moderator):
            raise Http404
    else:
        raise Http404
    if not f or not services.can_view(request.user, club) or not default_storage.exists(f.name):
        raise Http404
    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    resp = FileResponse(default_storage.open(f.name, "rb"), content_type=ctype)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "public, max-age=3600" if club.is_public else "private, no-store"
    return resp


# ---------------- кабинет клуба ----------------

def _my_club(user):
    return services.club_of(user)


@login_required
@require_http_methods(["GET", "POST"])
def mine(request):
    club = _my_club(request.user)
    if not club and not request.user.is_club:
        messages.info(request, "Профиль клуба ведёт аккаунт с ролью «Клуб». Добавьте её, если вы представляете клуб.")
        return redirect("accounts:add_role")
    form = ClubForm(request.POST or None, request.FILES or None, instance=club)
    form.submitting = "submit" in request.POST
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if request.POST.get("logo_clear") and "logo" not in request.FILES:
            obj.logo = None
        try:
            services.save(request.user, obj, form.changed_data)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("clubs:mine")
        if "submit" in request.POST:
            try:
                services.submit(request.user, obj.pk)
                messages.success(request, "Профиль отправлен модератору. Ответ придёт уведомлением, обычно в течение рабочего дня.")
            except ValueError as e:
                messages.error(request, str(e))
        elif obj.status == Club.Status.REVIEW and obj.is_public:
            messages.success(request, "Сохранено. Название или адрес изменились — профиль снова на проверке; до решения на странице прежние данные.")
        else:
            messages.success(request, "Сохранено.")
        return redirect("clubs:mine")
    news = club.news.prefetch_related("photos").select_related("tournament") if club else []
    invites = ClubMember.objects.filter(phone=request.user.phone, state=ClubMember.State.PENDING).select_related("club") if not club else []
    return render(request, "clubs/mine.html", {"club": club, "form": form, "news": news, "missing": services.missing(club) if club else [],
                                               "invites": invites, "is_owner": club and services.is_owner(request.user, club),
                                               "similar": services.similar(club) if club and not club.is_public and club.settlement else []})


@login_required
@require_POST
def photo_add(request):
    club = _my_club(request.user)
    if not club:
        raise Http404
    f = request.FILES.get("file")
    try:
        if not f:
            raise ValidationError("Выберите файл.")
        services.check_image(f)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("clubs:mine")
    p = ClubPhoto.objects.create(club=club, file=f, caption=request.POST.get("caption", "")[:200], position=club.photos.count())
    audit(request.user, "club.photo.add", club, photo=p.pk)
    messages.success(request, "Фото добавлено.")
    return redirect("clubs:mine")


@login_required
@require_POST
def photo_delete(request, pk):
    p = get_object_or_404(ClubPhoto.objects.select_related("club"), pk=pk)
    services.require_manage(request.user, p.club)
    p.delete()
    audit(request.user, "club.photo.delete", p.club, photo=pk)
    messages.success(request, "Фото удалено.")
    return redirect("clubs:mine")


@login_required
@require_http_methods(["GET", "POST"])
def news_edit(request, pk=None):
    club = _my_club(request.user)
    if not club:
        raise Http404
    item = get_object_or_404(ClubNews, pk=pk, club=club) if pk else None
    form = NewsForm(club, request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            n = form.save(commit=False)
            n.club = club
            n.save()
            photo = form.cleaned_data.get("photos")
            if photo:
                NewsPhoto.objects.create(news=n, file=photo, position=n.photos.count())
            audit(request.user, "club.news.save", club, news=n.pk)
        messages.success(request, "Новость опубликована." if not pk else "Новость сохранена.")
        return redirect("clubs:mine")
    return render(request, "clubs/news_form.html", {"club": club, "form": form, "item": item})


@login_required
@require_POST
def news_delete(request, pk):
    n = get_object_or_404(ClubNews.objects.select_related("club"), pk=pk)
    services.require_manage(request.user, n.club)
    title = n.title
    n.delete()
    audit(request.user, "club.news.delete", n.club, title=title)
    messages.success(request, "Новость удалена.")
    return redirect("clubs:mine")


# ---------------- модерация ----------------

@moderator_required
def mod_queue(request):
    waiting = Club.objects.filter(status=Club.Status.REVIEW, merged_into__isnull=True).select_related("manager").order_by("submitted_at")
    q = request.GET.get("q", "").strip()
    recent = Club.objects.exclude(status__in=[Club.Status.REVIEW, Club.Status.DRAFT]).select_related("merged_into")
    if q:
        recent = recent.filter(Q(name__icontains=q) | Q(settlement__icontains=q) | Q(pk=int(q)) if q.isdigit() else Q(name__icontains=q) | Q(settlement__icontains=q))
    return render(request, "clubs/mod_queue.html", {"waiting": waiting, "recent": recent.order_by("-updated_at")[:30], "q": q})


@moderator_required
@require_http_methods(["GET", "POST"])
def mod_detail(request, pk):
    club = get_object_or_404(Club.objects.select_related("manager"), pk=pk)
    if request.method == "POST":
        try:
            if request.POST.get("decision") == "confirm":
                services.confirm(request.user, club.pk)
                messages.success(request, "Клуб подтверждён и опубликован.")
            elif request.POST.get("decision") == "return":
                services.return_for_fixes(request.user, club.pk, request.POST.get("comment"))
                messages.success(request, "Клуб возвращён с замечаниями.")
            elif request.POST.get("decision") == "merge":
                main = (request.POST.get("main_id") or request.POST.get("main") or "").strip().lstrip("№#")
                if not main.isdigit():
                    raise ValueError("Укажите номер основного клуба.")
                services.merge(request.user, club.pk, int(main))
                messages.success(request, "Дубль объединён с основным клубом.")
            elif request.POST.get("decision") == "hide_news":
                services.hide_news(request.user, int(request.POST.get("news")), request.POST.get("reason"))
                messages.success(request, "Новость скрыта.")
            return redirect("moderation:clubs")
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, "clubs/mod_detail.html", {"club": club, "similar": services.similar(club), "news": club.news.all()[:20],
                                                     "managers": services.managers(club)})


# ---------------- управляющие ----------------

@login_required
@require_http_methods(["GET", "POST"])
def team(request):
    club = _my_club(request.user)
    if not club:
        raise Http404
    owner = services.is_owner(request.user, club)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "invite":
                services.invite_manager(request.user, club.pk, request.POST.get("phone", ""))
                messages.success(request, "Приглашение отправлено. Человек примет его в кабинете — после этого сможет править клуб.")
            elif action == "remove":
                m = get_object_or_404(ClubMember, pk=request.POST.get("member"), club=club)
                leaving = m.user_id == request.user.pk
                services.remove_manager(request.user, m.pk)
                messages.success(request, "Вы больше не управляете клубом." if leaving else "Управляющий удалён.")
                if leaving:
                    return redirect("accounts:cabinet")
            elif action == "transfer":
                m = get_object_or_404(ClubMember, pk=request.POST.get("member"), club=club)
                if request.POST.get("confirm") != "1":
                    return render(request, "clubs/transfer_confirm.html", {"club": club, "m": m})
                services.transfer(request.user, club.pk, m.pk)
                messages.success(request, f"Владелец клуба теперь {m.user.short_name}. Вы остались управляющим.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        except ValueError as e:
            messages.error(request, str(e))
        return redirect("clubs:team")
    members = club.members.select_related("user").exclude(state=ClubMember.State.DECLINED)
    return render(request, "clubs/team.html", {"club": club, "owner": owner, "members": members})


@login_required
@require_POST
def invite_respond(request, pk):
    try:
        m = services.respond(request.user, pk, request.POST.get("decision") == "accept")
    except ClubMember.DoesNotExist:
        raise Http404
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("tournaments:invitations")
    if m.state == ClubMember.State.ACCEPTED:
        messages.success(request, f"Теперь вы управляете клубом «{m.club.name}».")
        return redirect("clubs:mine")
    messages.success(request, "Приглашение отклонено.")
    return redirect("tournaments:invitations")
