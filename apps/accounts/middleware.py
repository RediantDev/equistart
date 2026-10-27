from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

ALLOWED_PREFIXES = ("/verify-phone/", "/logout/", "/static/", "/health", "/privacy/", "/consent/", "/publication-consent/", "/manage/")


class PhoneVerificationMiddleware:
    """Пока телефон не подтверждён, вошедший пользователь видит только экран подтверждения."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if (settings.PHONE_VERIFICATION_REQUIRED and u is not None and u.is_authenticated and not u.phone_verified
                and not u.is_superuser and not request.path.startswith(ALLOWED_PREFIXES)):
            return redirect(reverse("accounts:verify_phone") + "?next=" + request.get_full_path())
        return self.get_response(request)
