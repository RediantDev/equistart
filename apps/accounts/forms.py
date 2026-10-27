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
        self.widget.input_type = "tel"  # без дубля атрибута type — иначе на телефоне не открывается цифровая клавиатура
        self.widget.attrs.update({"inputmode": "tel", "autocomplete": "tel", "placeholder": "+7 900 000-00-00"})

    def clean(self, value):
        value = super().clean(value)
        return normalize_phone(value)


class RegistrationForm(forms.Form):
    phone = PhoneField(help_text="Россия и Казахстан — +7. Другие страны — с «+» и кодом страны: +375…, +998…")
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
        label="Кем вы будете в СтартЛисте",
        choices=Category.choices,
        widget=forms.CheckboxSelectMultiple,
        help_text="Можно выбрать несколько. Роли можно добавить или убрать позже в профиле.",
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
        if "athlete" in data["categories"]:
            # Профиль спортсмена сразу из данных регистрации: остаётся дописать дату рождения и разряд (отчёт 22.10.2026)
            from apps.profiles.models import Athlete

            Athlete.objects.create(last_name=user.last_name, first_name=user.first_name, middle_name=user.middle_name,
                                   manager=user, linked_user=user, consent_confirmed_at=timezone.now())
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
        self.fields["phone"].widget.input_type = "tel"
        self.fields["phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "username", "placeholder": "+7 900 000-00-00"})

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
    # Тест 5, п. 22.1: можно выбрать сразу несколько ролей
    category = forms.MultipleChoiceField(label="Роли", choices=Category.choices, widget=forms.CheckboxSelectMultiple,
                                         error_messages={"required": "Выберите хотя бы одну роль."})

    def __init__(self, user: User, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["category"].choices = [(c.value, c.label) for c in user.missing_categories()]

    def save(self):
        cats = self.cleaned_data["category"]
        for cat in cats:
            self.user.add_category(cat)
        self.user.save(update_fields=list(CATEGORY_FIELDS.values()))
        return cats


class ProfileForm(forms.Form):
    """Сведения о себе в профиле."""

    last_name = forms.CharField(label="Фамилия", max_length=100)
    first_name = forms.CharField(label="Имя", max_length=100)
    middle_name = forms.CharField(label="Отчество", max_length=100, required=False)
    email = forms.EmailField(label="Email для связи", required=False)


class PhoneChangeForm(forms.Form):
    phone = PhoneField(label="Новый телефон")
    password = forms.CharField(label="Текущий пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
                               help_text="Для защиты аккаунта: телефон — это логин.")

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        if phone == self.user.phone:
            raise ValidationError("Это ваш текущий номер.")
        if User.objects.filter(phone=phone).exclude(pk=self.user.pk).exists():
            raise ValidationError("Этот номер уже зарегистрирован в другом аккаунте.")
        return phone

    def clean_password(self):
        pw = self.cleaned_data["password"]
        if not self.user.check_password(pw):
            raise ValidationError("Неверный пароль.")
        return pw


class PasswordResetRequestForm(forms.Form):
    """Восстановление пароля, шаг 1: номер телефона (тест 5, п. 6.3)."""

    phone = forms.CharField(label="Телефон", max_length=32)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].widget.input_type = "tel"
        self.fields["phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "username", "placeholder": "+7 900 000-00-00"})

    def clean_phone(self):
        try:
            return normalize_phone(self.cleaned_data["phone"])
        except ValidationError:
            raise ValidationError("Проверьте номер: например, +7 900 000-00-00.")


class PasswordResetConfirmForm(forms.Form):
    """Шаг 2: код из СМС и новый пароль."""

    code = forms.CharField(label="Код из СМС", max_length=8)
    password1 = forms.CharField(label="Новый пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                                help_text="Не короче 8 символов, не только цифры.")
    password2 = forms.CharField(label="Повторите пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, user=None, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs.update({"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": "6", "class": "input-lg"})

    def clean(self):
        from django.contrib.auth.password_validation import validate_password

        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Пароли не совпадают.")
        elif p1:
            try:
                validate_password(p1, self.user)
            except ValidationError as e:
                self.add_error("password1", e)
        return cleaned
