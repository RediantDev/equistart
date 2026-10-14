from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
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
    user = request.user
    return render(
        request,
        "accounts/cabinet.html",
        {"mode": user.active_mode(), "modes": user.available_modes(), "can_add_role": bool(user.missing_categories())},
    )


@login_required
@require_POST
def switch_mode(request):
    mode = request.POST.get("mode", "")
    if mode not in {m for m, _ in request.user.available_modes()}:
        return HttpResponseBadRequest("Недоступный режим")
    request.user.current_mode = mode
    request.user.save(update_fields=["current_mode"])
    return redirect("accounts:cabinet")


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
