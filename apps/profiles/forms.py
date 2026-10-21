import datetime

from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.phone import normalize_phone

from .models import Athlete, Horse, HorseDocument, normalize_fksr

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
DOC_MAX = 10 * 1024 * 1024
DOC_SIGNATURES = {b"%PDF-": "application/pdf", b"\x89PNG\r": "image/png", b"\xff\xd8\xff": "image/jpeg"}


class AthleteForm(forms.ModelForm):
    consent = forms.BooleanField(
        label="Спортсмен (а за несовершеннолетнего — законный представитель) согласен на обработку данных и публикацию в протоколах",
        required=False,
    )

    class Meta:
        model = Athlete
        fields = ["last_name", "first_name", "middle_name", "birth_date", "rank_jumping", "rank_dressage", "fksr_number", "fei_id", "club", "region", "coach_name"]
        widgets = {"birth_date": DATE}
        help_texts = {
            "birth_date": "Нужна для зачётов с возрастными условиями. Публично показывается только год.",
            "rank_jumping": "Нужен для зачётов конкура с требованием разряда.",
            "rank_dressage": "Нужен для зачётов выездки с требованием разряда.",
            "fksr_number": "Необязательно. Один номер — один спортсмен в сервисе.",
            "fei_id": "Необязательно. Для спортсменов с регистрацией FEI.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("rank_jumping", "rank_dressage"):
            self.fields[f].choices = [("", "— не указан —")] + list(self.fields[f].choices)[1:]
        if self.instance.pk and self.instance.consent_confirmed_at:
            self.fields["consent"].initial = True

    def clean_birth_date(self):
        d = self.cleaned_data.get("birth_date")
        if d and (d > datetime.date.today() or d.year < 1920):
            raise ValidationError("Проверьте дату рождения.")
        return d

    def _unique(self, field, label):
        n = normalize_fksr(self.cleaned_data.get(field, ""))
        if n:
            dup = Athlete.objects.filter(**{field: n}, archived=False).exclude(pk=self.instance.pk).first()
            if dup:
                raise ValidationError(f"Спортсмен с {label} {n} уже есть в сервисе ({dup.public_name}). Попросите его управляющего дать вам разрешение на заявки или напишите модератору.")
        return n

    def clean_fksr_number(self):
        return self._unique("fksr_number", "номером ФКСР")

    def clean_fei_id(self):
        return self._unique("fei_id", "FEI ID")

    def clean(self):
        c = super().clean()
        if not c.get("consent") and not (self.instance.pk and self.instance.consent_confirmed_at):
            self.add_error("consent", "Без согласия спортсмена его нельзя добавить в заявку и протоколы.")
        return c


class HorseForm(forms.ModelForm):
    class Meta:
        model = Horse
        fields = ["name", "birth_year", "color", "sex", "breed", "fksr_number", "fei_id", "sire", "birthplace", "owner_name", "is_pony", "height_cm"]
        help_texts = {"fksr_number": "Необязательно. Если номер уже есть в сервисе, мы предложим найденный профиль.",
                      "fei_id": "Необязательно. Один номер — одна лошадь в сервисе."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicate = None
        self.fields["color"].choices = [("", "— выберите —")] + list(self.fields["color"].choices)[1:]
        self.fields["sex"].choices = [("", "— выберите —")] + list(self.fields["sex"].choices)[1:]

    def clean_birth_year(self):
        y = self.cleaned_data["birth_year"]
        if y > datetime.date.today().year or y < 1980:
            raise ValidationError("Год рождения не может быть из будущего или раньше 1980.")
        return y

    def clean_fksr_number(self):
        n = normalize_fksr(self.cleaned_data.get("fksr_number", ""))
        if n:
            dup = Horse.objects.filter(fksr_number=n, archived=False).exclude(pk=self.instance.pk).first()
            if dup:
                self.duplicate = dup
                raise ValidationError(f"Лошадь с номером ФКСР {n} уже есть в сервисе: {dup.name} ({dup.summary()}). Выберите её в заявке или напишите модератору.")
        return n

    def clean_height_cm(self):
        h = self.cleaned_data.get("height_cm")
        if h is not None and not 70 <= h <= 200:
            raise ValidationError("Рост в холке — от 70 до 200 см.")
        return h

    def clean_fei_id(self):
        n = normalize_fksr(self.cleaned_data.get("fei_id", ""))
        if n:
            dup = Horse.objects.filter(fei_id=n, archived=False).exclude(pk=self.instance.pk).first()
            if dup:
                self.duplicate = dup
                raise ValidationError(f"Лошадь с FEI ID {n} уже есть в сервисе: {dup.name} ({dup.summary()}). Выберите её в заявке или напишите модератору.")
        return n


class DocumentForm(forms.ModelForm):
    class Meta:
        model = HorseDocument
        fields = ["kind", "title", "file", "valid_until"]
        widgets = {"valid_until": DATE}
        help_texts = {"file": "PDF, JPEG или PNG до 10 МБ."}

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > DOC_MAX:
            raise ValidationError("Файл больше 10 МБ.")
        head = f.read(5)
        f.seek(0)
        for sig, ct in DOC_SIGNATURES.items():
            if head.startswith(sig):
                self.content_type = ct
                return f
        raise ValidationError("Нужен файл PDF, JPEG или PNG.")


class PhoneForm(forms.Form):
    phone = forms.CharField(label="Телефон", max_length=32)

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])
