from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .forms import AddRoleForm, LoginForm, RegistrationForm


def _safe_next(request, default="accounts:cabinet"):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return nxt
    return default


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:cabinet")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="apps.accounts.backends.PhoneBackend")
        messages.success(request, "Аккаунт создан. Добро пожаловать в EquiStart!")
        return redirect("accounts:cabinet")
    return render(request, "accounts/register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        return redirect(_safe_next(request))
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("core:calendar")


@login_required
def cabinet(request):
    """UI-07: «Главная» — разделы по ролям пользователя, без переключателя режимов."""
    import datetime

    from django.db.models import Q
    from django.utils import timezone

    from apps.entries.models import ChangeProposal, Entry
    from apps.profiles.models import Athlete, Offer
    from apps.tournaments.models import TeamMember, Tournament

    user = request.user
    today = timezone.localdate()
    team = TeamMember.objects.filter(user=user, state=TeamMember.State.ACCEPTED)
    judged_ids = set(team.filter(role__in=[TeamMember.Role.JUDGE, TeamMember.Role.ORGANIZER]).values_list("tournament_id", flat=True))
    judged_ids |= set(Tournament.objects.filter(owner=user).values_list("pk", flat=True))
    stable_ids = set(team.filter(role__in=[TeamMember.Role.STABLE, TeamMember.Role.ORGANIZER]).values_list("tournament_id", flat=True))
    stable_ids |= set(Tournament.objects.filter(owner=user).values_list("pk", flat=True))
    live = Q(publication=Tournament.Publication.PUBLISHED)
    judging_today = list(
        Tournament.objects.filter(live, pk__in=judged_ids, pub_starts_on__lte=today + datetime.timedelta(days=1), pub_ends_on__gte=today)
        .order_by("pub_starts_on")
    )

    actions = []
    n = TeamMember.objects.filter(phone=user.phone, state=TeamMember.State.PENDING).count()
    if n:
        actions.append(("Приглашения в команду турнира", n, "tournaments:invitations"))
    n = Offer.objects.filter(phone=user.phone, state=Offer.State.PENDING).count()
    if n:
        actions.append(("Предложения: связь с профилем или передача управления", n, "profiles:offers"))
    if judged_ids:
        pending = Entry.objects.filter(tournament__in=judged_ids, tournament__publication=Tournament.Publication.PUBLISHED, decision=Entry.Decision.PENDING)
        for t_id, title in pending.values_list("tournament_id", "tournament__pub_title").distinct():
            cnt = pending.filter(tournament_id=t_id).count()
            actions.append((f"Заявки на рассмотрении: {title}", cnt, ("entries:manage", t_id)))
        props = ChangeProposal.objects.filter(entry__tournament__in=judged_ids, state=ChangeProposal.State.PENDING).count()
        if props:
            actions.append(("Предложения изменения в заявках", props, None))
    if stable_ids:
        from apps.stables.models import Booking

        for t_id, title in (Booking.objects.filter(tournament__in=stable_ids, state=Booking.State.PENDING)
                            .values_list("tournament_id", "tournament__pub_title").distinct()):
            cnt = Booking.objects.filter(tournament_id=t_id, state=Booking.State.PENDING).count()
            actions.append((f"Брони денников ждут подтверждения: {title}", cnt, ("stables:manage", t_id)))
    if user.is_moderator:
        n = Tournament.objects.filter(status=Tournament.Status.REVIEW).count()
        if n:
            actions.append(("Турниры на проверке", n, "moderation:queue"))

    rows = []
    for label, count, target in actions:
        if isinstance(target, tuple):
            url = reverse(target[0], args=[target[1]])
        elif target:
            url = reverse(target)
        else:
            url = ""
        rows.append({"label": label, "count": count, "url": url})

    return render(
        request,
        "accounts/cabinet.html",
        {
            "labels": user.category_labels(),
            "can_add_role": bool(user.missing_categories()),
            "actions": rows,
            "judging_today": judging_today,
            "has_team_roles": bool(judged_ids or stable_ids),
            "own_athlete": Athlete.objects.filter(linked_user=user).first(),
            "today": today,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def add_role(request):
    if not request.user.missing_categories():
        messages.info(request, "У вас уже есть все доступные роли.")
        return redirect("accounts:cabinet")
    form = AddRoleForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Роль добавлена.")
        return redirect("accounts:cabinet")
    return render(request, "accounts/add_role.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def verify_phone(request):
    from . import verification

    nxt = request.GET.get("next") or request.POST.get("next") or ""
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else reverse("accounts:cabinet")
    if request.user.phone_verified:
        return redirect(nxt)
    sent = request.session.get("phone_code_sent", False)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "send":
                verification.send_code(request.user)
                request.session["phone_code_sent"] = True
                messages.success(request, f"Код отправлен на {request.user.phone_display}.")
                return redirect(reverse("accounts:verify_phone") + "?next=" + nxt)
            verification.check_code(request.user, request.POST.get("code", ""))
            request.session.pop("phone_code_sent", None)
            messages.success(request, "Телефон подтверждён.")
            return redirect(nxt)
        except ValueError as e:
            messages.error(request, str(e))
    return render(request, "accounts/verify_phone.html", {"sent": sent, "next": nxt, "wait": verification.seconds_until_resend(request.user)})


@login_required
@require_http_methods(["GET", "POST"])
def change_password(request):
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm

    from apps.core.models import audit

    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        audit(user, "user.password_change", user)
        messages.success(request, "Пароль изменён.")
        return redirect("accounts:cabinet")
    return render(request, "accounts/password.html", {"form": form})
