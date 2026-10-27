from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError

from .phone import normalize_phone


class PhoneBackend(ModelBackend):
    """Вход по телефону в любом привычном формате и паролю."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        phone = username if username is not None else kwargs.get("phone")
        if phone is None or password is None:
            return None
        try:
            phone = normalize_phone(phone)
        except ValidationError:
            return None
        return super().authenticate(request, username=phone, password=password)
