import datetime
from decimal import Decimal, InvalidOperation

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.accounts.phone import normalize_phone

from .disciplines import DISCIPLINE_CHOICES, PROGRAM_TYPE_FIELDS, program_type_choices, types_for
from .models import ProgramItem, TeamMember, Tournament, TournamentClass, Venue
from .regions import MULTI_ZONE, REGION_CHOICES, region_timezone

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
REGULATION_MAX_BYTES = 10 * 1024 * 1024


class BasicsForm(forms.Form):
    """Шаг 1: основные сведения и место проведения."""

    title = forms.CharField(label="Название", max_length=200)
    starts_on = forms.DateField(label="Дата проведения", widget=DATE)
    multiday = forms.BooleanField(label="Соревнование проходит несколько дней", required=False)
    ends_on = forms.DateField(label="Дата окончания", widget=DATE, required=False)
    venue = forms.ModelChoiceField(
        label="Место проведения", queryset=Venue.objects.none(), required=False, empty_label="— новое место —"
    )
    venue_name = forms.CharField(label="Клуб или место", max_length=200, required=False)
    venue_region = forms.ChoiceField(label="Регион", choices=[("", "— выберите —")] + REGION_CHOICES, required=False)
    venue_settlement = forms.CharField(label="Населённый пункт", max_length=120, required=False)
    venue_address = forms.CharField(label="Адрес", max_length=300, required=False)
    venue_timezone = forms.ChoiceField(
        label="Часовой пояс района", required=False,
        choices=[("", "— выберите —")] + [c for zones in MULTI_ZONE.values() for c in zones],
        help_text="Нужен только для Якутии: в республике три часовых пояса.",
    )
    organizer_name = forms.CharField(
        label="Кто проводит соревнование", max_length=200,
        help_text="Клуб, федерация или организатор — так будет указано на странице турнира.",
    )
    contact = forms.CharField(
        label="Контакт для участников", max_length=200, required=False,
        help_text="Телефон или email. Не публикуется — виден участникам после подачи заявки.",
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["venue"].queryset = Venue.objects.filter(Q(created_by=user) | Q(confirmed=True)).order_by("name")

    def clean(self):
        c = super().clean()
        start, end = c.get("starts_on"), c.get("ends_on")
        if c.get("multiday"):
            if not end:
                self.add_error("ends_on", "Укажите дату окончания.")
            elif start and end <= start:
                self.add_error("ends_on", "Дата окончания должна быть позже даты начала.")
            elif start and (end - start).days > 30:
                self.add_error("ends_on", "Соревнование не может длиться больше 31 дня.")
        else:
            c["ends_on"] = start
        if not c.get("venue"):
            for f in ("venue_name", "venue_region", "venue_settlement"):
                if not c.get(f):
                    self.add_error(f, "Выберите место из списка или заполните это поле.")
            region = c.get("venue_region")
            if region:
                try:
                    c["venue_tz"] = region_timezone(region, c.get("venue_timezone") or None)
                except ValueError:
                    self.add_error("venue_timezone", "Выберите часовой пояс района.")
        return c


class ProgramForm(forms.ModelForm):
    """Шаг 2: элемент программы."""

    program_type = forms.ChoiceField(label="Вид программы")

    class Meta:
        model = ProgramItem
        fields = ["day", "start_time", "discipline", "name", "program_type", "limit"]
        widgets = {"start_time": TIME}
        labels = {"limit": "Лимит участников"}

    def __init__(self, tournament, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tournament = tournament
        days = tournament.days()
        self.fields["day"] = forms.TypedChoiceField(
            label="День",
            choices=[(d.isoformat(), d.strftime("%d.%m.%Y")) for d in days],
            coerce=datetime.date.fromisoformat,
        )
        if len(days) == 1:
            self.fields["day"].initial = days[0].isoformat()
            self.fields["day"].widget = forms.HiddenInput()
        self.fields["program_type"].choices = [("", "— выберите —")] + program_type_choices()
        self.fields["discipline"].choices = [("", "— выберите —")] + DISCIPLINE_CHOICES
        self.fields["limit"].required = False
        self.fields["start_time"].required = False

    def clean(self):
        c = super().clean()
        d, t = c.get("discipline"), c.get("program_type")
        if d and t and t not in types_for(d):
            self.add_error("program_type", "Этот вид программы не относится к выбранной дисциплине.")
        return c

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.tournament = self.tournament
        if not obj.result_fields or "program_type" in self.changed_data:
            obj.result_fields = list(PROGRAM_TYPE_FIELDS.get(obj.program_type, ["points"]))
        if commit:
            obj.save()
        return obj


class RubField(forms.CharField):
    """Сумма в рублях с копейками → целые копейки."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.widget.attrs.update({"inputmode": "decimal", "placeholder": "2 500"})

    def clean(self, value):
        value = super().clean(value)
        raw = (value or "").replace(" ", "").replace(" ", "").replace("₽", "").replace(",", ".")
        try:
            rub = Decimal(raw)
        except InvalidOperation:
            raise ValidationError("Введите сумму числом, например 2500 или 2500,50.")
        if rub < 0:
            raise ValidationError("Сумма не может быть отрицательной.")
        if rub > Decimal("10000000"):
            raise ValidationError("Слишком большая сумма.")
        kop = rub * 100
        if kop != kop.to_integral_value():
            raise ValidationError("Не больше двух знаков после запятой.")
        return int(kop)


class ClassForm(forms.ModelForm):
    """Шаг 3: зачёт и цена."""

    fee = RubField(label="Стартовый взнос, ₽", help_text="0 — бесплатно.")

    class Meta:
        model = TournamentClass
        fields = ["name", "composition", "min_age", "max_age", "rank_requirement", "limit"]
        labels = {"rank_requirement": "Разряд / звание", "limit": "Лимит участников"}
        help_texts = {"rank_requirement": "Необязательно. Например: «II разряд и выше»."}

    def __init__(self, program, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        if self.instance.pk:
            k = self.instance.fee_kopecks
            self.fields["fee"].initial = f"{k // 100}" + (f",{k % 100:02d}" if k % 100 else "")
        for f in ("min_age", "max_age", "limit"):
            self.fields[f].required = False

    def clean(self):
        c = super().clean()
        lo, hi = c.get("min_age"), c.get("max_age")
        if lo and hi and hi < lo:
            self.add_error("max_age", "Верхняя граница возраста меньше нижней.")
        return c

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.program = self.program
        obj.fee_kopecks = self.cleaned_data["fee"]
        if commit:
            obj.save()
        return obj


class TermsForm(forms.ModelForm):
    """Шаг 4: положение, срок подачи, условия отмены."""

    class Meta:
        model = Tournament
        fields = ["regulation", "entry_deadline", "cancel_terms"]
        widgets = {
            "entry_deadline": DATE,
            "cancel_terms": forms.Textarea(attrs={"rows": 4}),
            "regulation": forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        }
        help_texts = {
            "entry_deadline": "До 23:59 этого дня по времени места проведения.",
            "cancel_terms": "Например: «Полный возврат взноса при отмене до 18 ноября; после — взнос не возвращается». Публикуется на странице турнира.",
            "regulation": "PDF до 10 МБ. Проверяется модератором.",
        }

    def clean_regulation(self):
        f = self.cleaned_data.get("regulation")
        if f and hasattr(f, "read") and getattr(f, "size", None) is not None and f not in (self.instance.regulation,):
            if f.size > REGULATION_MAX_BYTES:
                raise ValidationError("Файл больше 10 МБ.")
            head = f.read(5)
            f.seek(0)
            if head != b"%PDF-":
                raise ValidationError("Нужен файл в формате PDF.")
        return f


class InviteForm(forms.Form):
    phone = forms.CharField(label="Телефон", max_length=32)
    role = forms.ChoiceField(label="Роль", choices=TeamMember.Role.choices)

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])
