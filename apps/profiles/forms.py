import datetime

from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.phone import normalize_phone

from .models import Athlete, Horse, HorseDocument, normalize_fksr

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
DOC_MAX = 20 * 1024 * 1024
DOC_SIGNATURES = {b"%PDF-": "application/pdf", b"\x89PNG\r": "image/png", b"\xff\xd8\xff": "image/jpeg"}


class AthleteForm(forms.ModelForm):

    class Meta:
        model = Athlete
        fields = ["last_name", "first_name", "middle_name", "birth_date", "rank", "fksr_number", "fei_id", "club", "region", "coach_name"]
        widgets = {"birth_date": DATE}
        help_texts = {
            "birth_date": "Нужна для зачётов с возрастными условиями. Публично показывается только год.",
            "rank": "Один для конкура и выездки. По нему проверяются зачёты с условием о разряде.",
            "fksr_number": "Необязательно. Один номер — один спортсмен в сервисе.",
            "fei_id": "Необязательно. Для спортсменов с регистрацией FEI.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "region" in self.fields:
            from .catalogs import regions_json

            self.fields["region"].widget.attrs.update({"data-suggest": regions_json(), "placeholder": "Начните вводить: «Моск…»"})
        self.fields["rank"].choices = [("", "— не указан —")] + list(self.fields["rank"].choices)[1:]

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
        from .catalogs import breeds_json

        self.fields["breed"].widget.attrs.update({"data-suggest": breeds_json(), "placeholder": "Начните вводить или выберите"})

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
        help_texts = {"file": "PDF, фото (JPEG, PNG, WEBP, HEIC) до 20 МБ."}

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > DOC_MAX:
            raise ValidationError("Файл больше 20 МБ.")
        head = f.read(16)
        f.seek(0)
        for sig, ct in DOC_SIGNATURES.items():
            if head.startswith(sig):
                self.content_type = ct
                return f
        # Тест 5, п. 16.1: фото с телефона — WEBP и HEIC (iPhone) тоже подходят
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            self.content_type = "image/webp"
            return f
        if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heif", b"hevc"):
            self.content_type = "image/heic"
            return f
        raise ValidationError("Подходят PDF, JPEG, PNG, WEBP или HEIC (фото с iPhone).")


class PhoneForm(forms.Form):
    phone = forms.CharField(label="Телефон", max_length=32)

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])
