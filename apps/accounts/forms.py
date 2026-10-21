from django import forms
from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import throttle
from .models import CATEGORY_FIELDS, Category, User
from .phone import normalize_phone

PD_CONSENT_VERSION = "2026-10-14"


class PhoneField(forms.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("label", "Телефон")
        kwargs.setdefault("max_length", 32)
        super().__init__(**kwargs)
        self.widget.attrs.update(
            {"type": "tel", "inputmode": "tel", "autocomplete": "tel", "placeholder": "+7 900 000-00-00"}
        )

    def clean(self, value):
        value = super().clean(value)
        return normalize_phone(value)


class RegistrationForm(forms.Form):
    phone = PhoneField()
    password1 = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Не короче 8 символов, не только цифры.",
    )
    password2 = forms.CharField(
        label="Пароль ещё раз",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    last_name = forms.CharField(label="Фамилия", max_length=100, widget=forms.TextInput(attrs={"autocomplete": "family-name"}))
    first_name = forms.CharField(label="Имя", max_length=100, widget=forms.TextInput(attrs={"autocomplete": "given-name"}))
    middle_name = forms.CharField(
        label="Отчество", max_length=100, required=False, widget=forms.TextInput(attrs={"autocomplete": "additional-name"})
    )
    email = forms.EmailField(
        label="Email для связи",
        required=False,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    categories = forms.MultipleChoiceField(
        label="Кем вы будете в EquiStart",
        choices=Category.choices,
        widget=forms.CheckboxSelectMultiple,
        help_text="Можно выбрать несколько и добавить другие позже.",
        error_messages={"required": "Выберите хотя бы одну роль."},
    )
    pd_consent = forms.BooleanField(
        label="Даю согласие на обработку персональных данных",
        error_messages={"required": "Без согласия регистрация невозможна."},
    )

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        if User.objects.filter(phone=phone).exists():
            raise ValidationError("Этот номер уже зарегистрирован. Войдите в аккаунт.", code="duplicate")
        return phone

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Пароли не совпадают.")
        if p1 and not self.has_error("password2"):
            probe = User(
                phone=cleaned.get("phone", ""),
                last_name=cleaned.get("last_name", ""),
                first_name=cleaned.get("first_name", ""),
                email=cleaned.get("email", ""),
            )
            try:
                password_validation.validate_password(p1, probe)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned

    def save(self) -> User:
        data = self.cleaned_data
        user = User(
            phone=data["phone"],
            last_name=data["last_name"].strip(),
            first_name=data["first_name"].strip(),
            middle_name=data.get("middle_name", "").strip(),
            email=data.get("email", ""),
            pd_consent_at=timezone.now(),
            pd_consent_version=PD_CONSENT_VERSION,
        )
        for cat in data["categories"]:
            user.add_category(cat)
        user.set_password(data["password1"])
        user.save()
        return user


class LoginForm(forms.Form):
    phone = forms.CharField(label="Телефон", max_length=32)
    password = forms.CharField(
        label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    error_messages = {
        "invalid_login": "Неверный телефон или пароль.",
        "locked": "Слишком много неудачных попыток. Попробуйте через 15 минут.",
        "inactive": "Аккаунт заблокирован. Обратитесь в поддержку.",
    }

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)
        self.fields["phone"].widget.attrs.update(
            {"type": "tel", "inputmode": "tel", "autocomplete": "username", "placeholder": "+7 900 000-00-00"}
        )

    def clean(self):
        raw_phone = self.cleaned_data.get("phone")
        password = self.cleaned_data.get("password")
        if not raw_phone or not password:
            return self.cleaned_data
        try:
            phone = normalize_phone(raw_phone)
        except ValidationError:
            phone = None
        ip = throttle.client_ip(self.request) if self.request else ""
        if throttle.is_locked(phone, ip):
            raise ValidationError(self.error_messages["locked"], code="locked")
        user = authenticate(self.request, username=raw_phone, password=password)
        if user is None:
            throttle.register_failure(phone, ip)
            # Сообщаем о блокировке только тому, кто знает верный пароль.
            blocked = User.objects.filter(phone=phone, is_active=False).first() if phone else None
            if blocked and blocked.check_password(password):
                raise ValidationError(self.error_messages["inactive"], code="inactive")
            raise ValidationError(self.error_messages["invalid_login"], code="invalid_login")
        throttle.reset(phone)
        self.user_cache = user
        return self.cleaned_data

    def get_user(self):
        return self.user_cache


class AddRoleForm(forms.Form):
    category = forms.ChoiceField(label="Роль", choices=Category.choices)

    def __init__(self, user: User, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["category"].choices = [(c.value, c.label) for c in user.missing_categories()]

    def save(self):
        cat = self.cleaned_data["category"]
        self.user.add_category(cat)
        self.user.save(update_fields=list(CATEGORY_FIELDS.values()))
        return cat
