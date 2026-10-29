import datetime

from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.phone import normalize_phone

from .models import FKSR_ERROR, Athlete, Horse, HorseDocument, fksr_valid, normalize_fksr

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
DOC_MAX = 20 * 1024 * 1024
DOC_SIGNATURES = {b"%PDF-": "application/pdf", b"\x89PNG\r": "image/png", b"\xff\xd8\xff": "image/jpeg"}


class AthleteForm(forms.ModelForm):

    class Meta:
        model = Athlete
        fields = ["last_name", "first_name", "middle_name", "birth_date", "rank", "fksr_number", "fei_id", "club", "region", "coach_name"]
        widgets = {"birth_date": DATE}

    # Решение заказчика 28.10.2026: дата рождения и разряд обязательны (б/р — допустимый выбор).
    # Прежние записи без них в БД остаются; заполнить их потребуется при следующем сохранении.
    REQUIRED = ("birth_date", "rank")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.help_text = ""  # Тест 5.1: без длинных подсказок под полями
        for name in self.REQUIRED:
            self.fields[name].required = True
            self.fields[name].error_messages["required"] = "Обязательное поле."
        if "region" in self.fields:
            from .catalogs import regions_json

            self.fields["region"].widget.attrs.update({"data-suggest": regions_json(), "placeholder": "Например: Моск…"})
        self.fields["rank"].choices = [("", "— выберите —")] + list(self.fields["rank"].choices)[1:]

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
        if not fksr_valid(self.cleaned_data.get("fksr_number", "")):
            raise ValidationError(FKSR_ERROR)
        return self._unique("fksr_number", "номером ФКСР")

    def clean_fei_id(self):
        return self._unique("fei_id", "FEI ID")

class HorseForm(forms.ModelForm):
    class Meta:
        model = Horse
        fields = ["name", "birth_year", "color", "sex", "breed", "fksr_number", "fei_id", "sire", "birthplace", "owner_name", "is_pony", "height_cm"]

    MAX_AGE = 35  # Тест 5.1: реалистичный возраст лошади

    @classmethod
    def year_bounds(cls):
        this = datetime.date.today().year
        return this - cls.MAX_AGE, this

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicate = None
        for name, f in self.fields.items():
            if name != "owner_name":
                f.help_text = ""  # Тест 5.1: без длинных подсказок; у владельца — короткая
        self.fields["owner_name"].help_text = "Не публикуется."
        lo, hi = self.year_bounds()
        self.fields["birth_year"].widget.attrs.update({"min": lo, "max": hi, "inputmode": "numeric", "placeholder": str(hi - 8)})
        # Макет R2_07A: короткие поля парами — короткие подписи и подсказки
        self.fields["color"].choices = [("", "выберите")] + list(self.fields["color"].choices)[1:]
        self.fields["sex"].choices = [("", "выберите")] + list(self.fields["sex"].choices)[1:]
        self.fields["fei_id"].label = "FEI ID"
        for name in ("fksr_number", "fei_id"):
            self.fields[name].widget.attrs["placeholder"] = "если есть"
        self.fields["sire"].widget.attrs["placeholder"] = "Кличка отца"
        from .catalogs import breeds_json

        self.fields["breed"].widget.attrs.update({"data-suggest": breeds_json(), "placeholder": "Введите"})

    def clean_birth_year(self):
        y = self.cleaned_data["birth_year"]
        lo, hi = self.year_bounds()
        if y > hi:
            raise ValidationError(f"Год рождения не может быть из будущего: укажите год от {lo} до {hi}.")
        if y < lo:
            raise ValidationError(f"Лошади не бывает больше {self.MAX_AGE} лет: укажите год от {lo} до {hi}.")
        return y

    def clean_fksr_number(self):
        n = normalize_fksr(self.cleaned_data.get("fksr_number", ""))
        if not fksr_valid(n):
            raise ValidationError(FKSR_ERROR)
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [("", "выберите")] + list(self.fields["kind"].choices)[1:]

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
