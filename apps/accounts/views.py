from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    AddRoleForm, LoginForm, PasswordResetConfirmForm, PasswordResetRequestForm, PhoneChangeForm, ProfileForm, RegistrationForm,
)
from .models import CATEGORY_FIELDS, Category


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
    if request.method == "POST" and not form.is_valid() and "phone" in form.errors and any(
            e.code == "duplicate" for e in form.errors.as_data()["phone"]):
        # Номер уже есть: показываем окно «Войти?», остальные ошибки (пароли) не мешают (тест 5.1, п. 11)
        for name in ("password1", "password2", "pd_consent"):
            form.errors.pop(name, None)
        return render(request, "accounts/register.html", {"form": form, "dup_phone": True})
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="apps.accounts.backends.PhoneBackend")
        messages.success(request, "Аккаунт создан. Добро пожаловать в СтартЛист!")
        return redirect(_safe_next(request))  # из «Подать заявку» — сразу обратно к заявке
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


def _club_of(user):
    from apps.clubs.services import club_of

    return club_of(user)


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
    from apps.clubs.models import ClubMember

    n = ClubMember.objects.filter(phone=user.phone, state=ClubMember.State.PENDING).count()
    if n:
        actions.append(("Приглашение управлять клубом", n, "tournaments:invitations"))
    n = Offer.objects.filter(phone=user.phone, state=Offer.State.PENDING).count()
    if n:
        actions.append(("Предложения: связь с профилем или передача управления", n, "profiles:offers"))
    if judged_ids:
        pending = Entry.objects.filter(tournament__in=judged_ids, tournament__publication=Tournament.Publication.PUBLISHED, decision=Entry.Decision.PENDING)
        for t_id, title in pending.order_by().values_list("tournament_id", "tournament__pub_title").distinct():
            cnt = pending.filter(tournament_id=t_id).count()
            actions.append((f"Заявки на рассмотрении: {title}", cnt, ("entries:manage", t_id)))
        props = ChangeProposal.objects.filter(entry__tournament__in=judged_ids, state=ChangeProposal.State.PENDING).count()
        if props:
            actions.append(("Предложения изменения в заявках", props, None))
    if stable_ids:
        from apps.stables.models import Booking

        for t_id, title in (Booking.objects.filter(tournament__in=stable_ids, state=Booking.State.PENDING)
                            .order_by().values_list("tournament_id", "tournament__pub_title").distinct()):
            cnt = Booking.objects.filter(tournament_id=t_id, state=Booking.State.PENDING).count()
            actions.append((f"Брони денников ждут подтверждения: {title}", cnt, ("stables:manage", t_id)))
    from apps.entries.models import ApplicationDraft

    own = Athlete.objects.filter(linked_user=user, archived=False).first()
    if own and not own.birth_date:
        actions.append(("Профиль спортсмена: укажите дату рождения — без неё недоступны зачёты с возрастом", None, ("profiles:athlete_edit", own.pk)))

    for d in ApplicationDraft.objects.filter(user=user, tournament__publication=Tournament.Publication.PUBLISHED).select_related("tournament"):
        n = len(d.data.get("lines", []))
        actions.append((f"Незавершённая заявка: {d.tournament.pub_title} — продолжить", n,
                        ("entries:apply", d.tournament_id) if not d.late else None))
    if user.is_moderator:
        n = Tournament.objects.filter(status=Tournament.Status.REVIEW).count()
        if n:
            actions.append(("Турниры на проверке", n, "moderation:queue"))
        from apps.clubs.models import Club

        n = Club.objects.filter(status=Club.Status.REVIEW).count()
        if n:
            actions.append(("Клубы на проверке", n, "moderation:clubs"))

    rows = []
    for label, count, target in actions:
        if isinstance(target, tuple):
            url = reverse(target[0], args=[target[1]])
        elif target:
            url = reverse(target)
        else:
            url = ""
        rows.append({"label": label, "count": count, "url": url})

    judge_tournaments = list(
        Tournament.objects.filter(pk__in=judged_ids).exclude(publication=Tournament.Publication.NONE).order_by("-starts_on")[:12]
    ) if judged_ids else []
    pend = {}
    if judge_tournaments:
        from django.db.models import Count

        pend = dict(Entry.objects.filter(tournament__in=judge_tournaments, decision=Entry.Decision.PENDING).order_by()
                    .values("tournament_id").annotate(n=Count("id")).values_list("tournament_id", "n"))
    for jt in judge_tournaments:
        jt.pending_count = pend.get(jt.pk, 0)
    return render(
        request,
        "accounts/cabinet.html",
        {
            "judge_tournaments": judge_tournaments,
            "labels": user.category_labels(),
            "can_add_role": bool(user.missing_categories()),
            "actions": rows,
            "judging_today": judging_today,
            "has_team_roles": bool(judged_ids or stable_ids),
            "my_club": _club_of(user),
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
        cats = form.save()
        messages.success(request, "Роли добавлены." if len(cats) > 1 else "Роль добавлена.")
        return redirect("accounts:profile")
    return render(request, "accounts/add_role.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    """Профиль: сведения о себе, телефон, пароль, роли, выход (тест 4, п. 12–16)."""
    from apps.core.models import audit

    user = request.user
    form = ProfileForm(request.POST or None, initial={"last_name": user.last_name, "first_name": user.first_name,
                                                      "middle_name": user.middle_name, "email": user.email})
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        user.last_name, user.first_name = c["last_name"].strip(), c["first_name"].strip()
        user.middle_name, user.email = c.get("middle_name", "").strip(), c.get("email", "")
        user.save(update_fields=["last_name", "first_name", "middle_name", "email"])
        audit(user, "user.profile_edit", user)
        messages.success(request, "Сведения сохранены.")
        return redirect("accounts:profile")
    roles = [{"value": c.value, "label": c.label, "on": getattr(user, CATEGORY_FIELDS[c])} for c in Category]
    return render(request, "accounts/profile.html", {"form": form, "roles": roles, "can_add_role": bool(user.missing_categories())})


@login_required
@require_http_methods(["GET", "POST"])
def change_phone(request):
    from apps.core.models import audit

    form = PhoneChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = request.user
        old = user.phone
        user.phone = form.cleaned_data["phone"]
        user.phone_verified_at = None
        user.save(update_fields=["phone", "phone_verified_at"])
        audit(user, "user.phone_change", user, old=old[-4:])
        messages.success(request, f"Телефон изменён. Теперь входите по номеру {user.phone_display}.")
        return redirect("accounts:profile")
    return render(request, "accounts/phone.html", {"form": form})


@login_required
@require_POST
def remove_role(request):
    from apps.core.models import audit

    user = request.user
    cat = request.POST.get("category", "")
    if cat not in CATEGORY_FIELDS or not getattr(user, CATEGORY_FIELDS[cat]):
        messages.error(request, "Такой роли у вас нет.")
        return redirect("accounts:profile")
    if len(user.categories()) <= 1:
        messages.error(request, "Нельзя убрать единственную роль — сначала добавьте другую.")
        return redirect("accounts:profile")
    if cat == Category.CLUB and _club_of(user):
        messages.error(request, "Вы управляете клубом. Передайте управление другому человеку, затем уберите роль «Клуб».")
        return redirect("accounts:profile")
    setattr(user, CATEGORY_FIELDS[cat], False)
    user.save(update_fields=[CATEGORY_FIELDS[cat]])
    audit(user, "user.role_remove", user, role=cat)
    messages.success(request, f"Роль «{Category(cat).label}» убрана. Данные сохранены — роль можно вернуть в любой момент.")
    return redirect("accounts:profile")


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
    form.fields["new_password1"].help_text = "Не короче 8 символов, не только цифры и не похож на телефон или имя."
    form.fields["new_password2"].help_text = ""
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        audit(user, "user.password_change", user)
        messages.success(request, "Пароль изменён.")
        return redirect("accounts:profile")
    return render(request, "accounts/password.html", {"form": form})


# ---------------- Восстановление пароля по СМС (тест 5, п. 6.3) ----------------

RESET_KEY = "pwreset_phone"


@require_http_methods(["GET", "POST"])
def password_reset(request):
    """Шаг 1. Не сообщаем, есть ли такой номер: ответ одинаковый (защита от перебора номеров)."""
    from . import verification
    from .models import User

    if request.user.is_authenticated:
        return redirect("accounts:password")
    form = PasswordResetRequestForm(request.POST or None, initial={"phone": request.GET.get("phone", "")})
    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        user = User.objects.filter(phone=phone, is_active=True).first()
        if user:
            try:
                verification.send_code(user, purpose="reset")
            except ValueError as e:
                form.add_error("phone", str(e))
                return render(request, "accounts/password_reset.html", {"form": form})
        request.session[RESET_KEY] = phone
        messages.success(request, "Если этот номер зарегистрирован, мы отправили на него код.")
        return redirect("accounts:password_reset_confirm")
    return render(request, "accounts/password_reset.html", {"form": form})


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request):
    from apps.core.models import audit

    from . import verification
    from .models import User

    phone = request.session.get(RESET_KEY)
    if not phone:
        return redirect("accounts:password_reset")
    user = User.objects.filter(phone=phone, is_active=True).first()
    form = PasswordResetConfirmForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            if not user:
                raise ValueError("Неверный код.")
            verification.check_code(user, form.cleaned_data["code"])
        except ValueError as e:
            form.add_error("code", str(e))
        else:
            user.set_password(form.cleaned_data["password1"])
            user.save(update_fields=["password"])
            audit(user, "password.reset", user)
            request.session.pop(RESET_KEY, None)
            login(request, user, backend="apps.accounts.backends.PhoneBackend")
            messages.success(request, "Пароль изменён — вы вошли в аккаунт.")
            return redirect("accounts:cabinet")
    from .phone import format_phone

    return render(request, "accounts/password_reset_confirm.html", {"form": form, "phone": format_phone(phone)})


def phone_check(request):
    """Есть ли аккаунт с таким номером — для окна «Войти?» при регистрации (тест 5.1, п. 11).

    Регистрация и так сообщает «номер уже зарегистрирован»; ограничение частоты — от перебора номеров.
    """
    from django.core.cache import cache
    from django.core.exceptions import ValidationError
    from django.http import JsonResponse

    from . import throttle
    from .models import User
    from .phone import normalize_phone

    key = "phone-check:" + throttle.client_ip(request)
    n = cache.get(key, 0)
    if n >= 30:
        return JsonResponse({"exists": False, "limited": True}, status=429)
    cache.set(key, n + 1, 600)
    try:
        phone = normalize_phone(request.GET.get("phone", ""))
    except ValidationError:
        return JsonResponse({"exists": False, "valid": False})
    return JsonResponse({"exists": User.objects.filter(phone=phone).exists(), "valid": True})
