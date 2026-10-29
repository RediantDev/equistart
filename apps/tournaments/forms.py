import datetime
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.accounts.phone import normalize_phone

from .models import ProgramItem, TeamMember, Tournament, TournamentClass, Venue
from .regions import MULTI_ZONE, REGION_CHOICES, region_timezone

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
REGULATION_MAX_BYTES = 20 * 1024 * 1024


WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _day_label(d):
    return f"{d.day} {MONTHS_GEN[d.month - 1]}, {WEEKDAYS[d.weekday()]}"


class BasicsForm(forms.Form):
    """Шаг 1: основные сведения и место проведения."""

    title = forms.CharField(label="Название", max_length=200)
    starts_on = forms.DateField(label="Дата начала", widget=DATE)
    multiday = forms.BooleanField(label="Соревнование проходит несколько дней", required=False)
    ends_on = forms.DateField(label="Дата окончания", widget=DATE, required=False)
    venue = forms.ModelChoiceField(
        label="Место проведения", queryset=Venue.objects.none(), required=False, empty_label="+ Новое место"
    )
    venue_name = forms.CharField(label="Клуб или место", max_length=200, required=False)
    venue_region = forms.ChoiceField(label="Регион", choices=[("", "— выберите —")] + REGION_CHOICES, required=False)
    venue_settlement = forms.CharField(label="Населённый пункт", max_length=120, required=False)
    venue_address = forms.CharField(label="Адрес", max_length=300, required=False)
    level = forms.ChoiceField(label="Статус соревнований", choices=Tournament.Level.choices, initial=Tournament.Level.CLUB, required=False)
    classification = forms.BooleanField(label="Классификационные (присвоение разрядов)", required=False,
                                        help_text="Отметьте, если по итогам соревнований присваиваются спортивные разряды.")
    contact_name = forms.CharField(label="Контакт для участников: имя", max_length=120, required=False)
    contact = forms.CharField(
        label="Контакт для участников: телефон", max_length=32,
        help_text="Виден всем на странице турнира.",
        widget=forms.TextInput(attrs={"type": "tel", "inputmode": "tel", "autocomplete": "tel"}),
        error_messages={"required": "Укажите телефон — участники увидят его на странице турнира."},
    )

    def clean_contact(self):
        # тест 4.2: телефон контакта вводится как при регистрации и хранится в одном формате
        return normalize_phone(self.cleaned_data["contact"])

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["venue"].queryset = Venue.objects.filter(Q(created_by=user) | Q(confirmed=True)).order_by("name")
        for f in ("venue", "venue_name", "venue_region", "venue_settlement", "ends_on", "level"):
            self.fields[f].show_required = True  # обязательны по условию — подпись «необязательно» не показываем

    def clean(self):
        c = super().clean()
        c["level"] = c.get("level") or Tournament.Level.CLUB
        start, end = c.get("starts_on"), c.get("ends_on")
        if start and getattr(self, "is_new", False) and start < datetime.date.today():
            self.add_error("starts_on", "Дата начала уже прошла — укажите будущую дату.")
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
                # Тест 4: поле «Часовой пояс района» убрано. Для многозонного региона — основной пояс региона.
                c["venue_tz"] = MULTI_ZONE[region][0][0] if region in MULTI_ZONE else region_timezone(region)
        return c


class ProgramForm(forms.ModelForm):
    """Шаг 2: маршрут конкура (тип из справочника, высоты, таблица) или езда выездки (из справочника езд)."""

    heights_text = forms.CharField(
        label="Высота, см", required=False, max_length=60,
        help_text="Одна высота или несколько через запятую: «60, 70, 80». Каждая высота — отдельная классификация.",
    )

    class Meta:
        model = ProgramItem
        fields = ["day", "start_time", "discipline", "name", "program_type", "variant", "table", "dressage_test"]
        widgets = {"start_time": TIME}
        labels = {"program_type": "Тип маршрута", "variant": "Вариант", "table": "Таблица штрафов", "dressage_test": "Езда"}

    def __init__(self, tournament, user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.sport.jumping import ROUTE_CHOICES, ROUTE_TYPES, TABLES
        from apps.sport.models import DressageTest

        self.tournament = tournament
        days = tournament.days()
        self.fields["day"] = forms.TypedChoiceField(
            label="Дата", choices=[(d.isoformat(), _day_label(d)) for d in days], coerce=datetime.date.fromisoformat,
        )
        if len(days) == 1:
            self.fields["day"].initial = days[0].isoformat()
            self.fields["day"].widget = forms.HiddenInput()
        self.fields["discipline"] = forms.ChoiceField(label="Дисциплина", choices=[("jumping", "Конкур"), ("dressage", "Выездка")],
                                                      widget=forms.RadioSelect, initial="jumping")
        self.fields["program_type"] = forms.ChoiceField(label="Тип маршрута", required=False, choices=[("", "— выберите —")] + ROUTE_CHOICES)
        variants = [(v, f"{r.name}: {label}") for r in ROUTE_TYPES for v, label in r.variants]
        self.fields["variant"] = forms.ChoiceField(label="Вариант формулы", required=False, choices=[("", "—")] + variants)
        self.fields["table"] = forms.ChoiceField(label="Таблица штрафов", required=False,
                                                 choices=[("", "по типу маршрута")] + [(k, v) for k, v in TABLES.items() if k != "STYLE"])
        owner = user or tournament.owner
        self.fields["dressage_test"].queryset = DressageTest.objects.filter(is_active=True).filter(Q(is_official=True) | Q(owner=owner))
        self.fields["dressage_test"].required = False
        self.fields["dressage_test"].empty_label = "— выберите езду —"
        self.fields["start_time"].required = False
        self.fields["name"].required = False
        self.fields["name"].help_text = "Можно не заполнять — название составится из номера, высоты и типа."
        if self.instance.pk and self.instance.heights:
            self.fields["heights_text"].initial = ", ".join(str(h) for h in self.instance.heights)
        # Тест 4: зачёты выбираются сразу при добавлении маршрута, можно несколько
        from apps.sport.models import ClassTemplate

        self.class_templates = list(ClassTemplate.objects.filter(is_active=True))
        self.fields["templates"] = forms.MultipleChoiceField(
            label="Зачёты", required=False, widget=forms.CheckboxSelectMultiple,
            choices=[(str(t.pk), t.name) for t in self.class_templates])
        self.fields["start_time"].label = "Начало"
        self.fields["program_type"].show_required = True
        self.fields["heights_text"].show_required = True
        self.fields["dressage_test"].show_required = True
        self.fields["variant"].show_required = True

    def clean(self):
        from apps.sport.jumping import route, table_allowed

        c = super().clean()
        d = c.get("discipline")
        if d == "jumping":
            key = c.get("program_type")
            if not key:
                self.add_error("program_type", "Выберите тип маршрута.")
                return c
            rt = route(key)
            raw = (c.get("heights_text") or "").replace(";", ",")
            try:
                heights = sorted({int(x.strip()) for x in raw.split(",") if x.strip()})
            except ValueError:
                self.add_error("heights_text", "Высоты — целые числа в сантиметрах через запятую.")
                return c
            if not heights and not rt.manual:
                self.add_error("heights_text", "Укажите высоту маршрута.")
            if any(h < 10 or h > 220 for h in heights):
                self.add_error("heights_text", "Высота — от 10 до 220 см.")
            c["heights"] = heights
            table = c.get("table") or rt.default_table
            if rt.tables and table not in rt.tables:
                self.add_error("table", f"Для «{rt.name}» допустимы таблицы: {', '.join(rt.tables)}.")
            msg = table_allowed(table, max(heights) if heights else None, self.tournament.is_official)
            if msg:
                self.add_error("table", msg)
            c["table"] = table if rt.tables else ""
            variants = [v for v, _ in rt.variants]
            if variants and c.get("variant") not in variants:
                c["variant"] = variants[0] if not c.get("variant") else ""
                if not c["variant"]:
                    self.add_error("variant", "Выберите вариант формулы этого маршрута.")
            elif not variants:
                c["variant"] = ""
            c["dressage_test"] = None
        elif d == "dressage":
            if not c.get("dressage_test"):
                self.add_error("dressage_test", "Выберите езду из справочника или создайте свою.")
            c["program_type"], c["variant"], c["table"], c["heights"] = "dressage_test", "", "", []
        return c

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.tournament = self.tournament
        if not obj.pk:
            obj.start_times = obj.discipline == "dressage"  # в выездке время стартов по умолчанию считается, судья может выключить
        obj.heights = self.cleaned_data.get("heights", [])
        obj.program_type = self.cleaned_data.get("program_type") or obj.program_type
        test = self.cleaned_data.get("dressage_test")
        if test and (not obj.test_snapshot or obj.test_snapshot.get("id") != test.pk or "dressage_test" in self.changed_data):
            obj.test_snapshot = test.snapshot()
            obj.arena = test.arena
            if test.duration_min:
                import math

                obj.slot_minutes = math.ceil(float(test.duration_min)) + 2
        if not test:
            obj.test_snapshot = None
        if obj.program_type == "manual" and not obj.result_fields:
            obj.result_fields = ["penalties", "time"]
        if not (obj.name or "").strip():
            obj.name = self.auto_name(obj)
        if commit:
            obj.save()
        return obj

    def auto_name(self, obj):
        if obj.discipline == "dressage" and obj.test_snapshot:
            return f"Езда «{obj.test_snapshot['name']}»"
        n = self.tournament.programs.filter(discipline="jumping").exclude(pk=obj.pk).count() + 1
        h = "-".join(str(x) for x in obj.heights)
        return f"Маршрут №{n}" + (f" · {h} см" if h else "")


class ClassForm(forms.ModelForm):
    """Зачёт: выбор из шаблонов или свой (TRN-04)."""

    class Meta:
        model = TournamentClass
        fields = ["name", "height", "birth_year_from", "birth_year_to", "rank_rule", "rank", "horse_age_min", "horse_age_max",
                  "horse_kind", "height_min", "height_max", "min_pairs", "note"]
        help_texts = {
            "birth_year_from": "Самый ранний год рождения (старшие всадники).",
            "birth_year_to": "Самый поздний год рождения (младшие всадники).",
            "rank_rule": "«Не ниже» — проверяется автоматически; «никогда не выполнявшие» участник подтверждает в заявке.",
            "min_pairs": "Если пар меньше, судья может объединить зачёт с другим.",
        }

    def __init__(self, program, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        for f in self.fields.values():
            f.required = False
        self.fields["name"].required = True
        if program.heights and len(program.heights) > 1:
            self.fields["height"] = forms.TypedChoiceField(
                label="Высота", coerce=int, empty_value=None, required=False,
                choices=[("", "для всех высот")] + [(h, f"{h} см") for h in program.heights])
        else:
            del self.fields["height"]

    def clean(self):
        c = super().clean()
        a, b = c.get("birth_year_from"), c.get("birth_year_to")
        if a and b and b < a:
            self.add_error("birth_year_to", "Год «по» раньше года «с».")
        for f in ("birth_year_from", "birth_year_to"):
            y = c.get(f)
            if y and not 1920 <= y <= datetime.date.today().year + 1:
                self.add_error(f, "Проверьте год рождения.")
        h1, h2 = c.get("horse_age_min"), c.get("horse_age_max")
        if h1 and h2 and h2 < h1:
            self.add_error("horse_age_max", "Верхняя граница меньше нижней.")
        if c.get("rank_rule") and not c.get("rank"):
            self.add_error("rank", "Выберите разряд для условия.")
        if c.get("rank") and not c.get("rank_rule"):
            self.add_error("rank_rule", "Выберите условие: «не ниже» или «никогда не выполнявшие».")
        return c

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.program = self.program
        if commit:
            obj.save()
        return obj


class TermsForm(forms.ModelForm):
    """Шаг 4: положение, срок подачи, условия отмены."""

    class Meta:
        model = Tournament
        fields = ["regulation", "entry_deadline", "entry_deadline_time", "cancel_terms"]
        widgets = {
            "entry_deadline": DATE,
            "entry_deadline_time": TIME,
            "cancel_terms": forms.Textarea(attrs={"rows": 4}),
            "regulation": forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        }
        help_texts = {
            "entry_deadline": "По времени места проведения.",
            "cancel_terms": "Например: «Отказ от старта — не позднее 18:00 накануне дня маршрута, через сайт или судье». Публикуется на странице турнира; пустое поле не показывается.",
            "regulation": "PDF до 20 МБ. Проверяется модератором.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["entry_deadline"].show_required = True  # нужен для отправки на проверку
        self.fields["entry_deadline"].label = "Последний день подачи заявок"
        self.fields["entry_deadline_time"].label = "До какого времени"
        self.fields["entry_deadline_time"].required = True  # тест 4.2: время обязательно
        self.fields["entry_deadline_time"].error_messages["required"] = "Укажите время окончания приёма заявок."
        if not self.instance.entry_deadline_time and not self.is_bound:
            self.initial["entry_deadline_time"] = "17:00"  # решение 29.10.2026: по умолчанию до 17:00

    def clean_regulation(self):
        f = self.cleaned_data.get("regulation")
        if f and hasattr(f, "read") and getattr(f, "size", None) is not None and f not in (self.instance.regulation,):
            if f.size > REGULATION_MAX_BYTES:
                raise ValidationError("Файл больше 20 МБ.")
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
