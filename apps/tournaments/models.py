import datetime
import zoneinfo

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from .disciplines import DISCIPLINE_CHOICES, DISCIPLINE_COLORS, PROGRAM_TYPE_NAMES, RESULT_FIELDS
from apps.profiles.models import RANKS

from .regions import REGION_CHOICES, REGION_NAMES

User = settings.AUTH_USER_MODEL


class Venue(models.Model):
    """Место проведения: клуб или иной адрес. Вводит организатор, подтверждает модератор."""

    name = models.CharField("клуб или место", max_length=200)
    region = models.CharField("регион", max_length=16, choices=REGION_CHOICES)
    settlement = models.CharField("населённый пункт", max_length=120)
    address = models.CharField("адрес", max_length=300, blank=True)
    timezone = models.CharField("часовой пояс", max_length=64)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="venues")
    confirmed = models.BooleanField("подтверждено модератором", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "место проведения"
        verbose_name_plural = "места проведения"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}, {self.settlement}"

    @property
    def region_name(self):
        return REGION_NAMES.get(self.region, "")

    def public_dict(self):
        return {
            "name": self.name,
            "region": self.region,
            "region_name": self.region_name,
            "settlement": self.settlement,
            "address": self.address,
        }


class Tournament(models.Model):
    class Level(models.TextChoices):
        CLUB = "club", "Клубные"
        MUNICIPAL = "municipal", "Муниципальные"
        REGIONAL = "regional", "Региональные"
        NATIONAL = "national", "Всероссийские"
        INTERNATIONAL = "international", "Международные"
        OTHER = "other", "Иной статус"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        REVIEW = "review", "На проверке"
        RETURNED = "returned", "Нужны исправления"
        APPROVED = "approved", "Опубликован без изменений"
        REJECTED = "rejected", "Отклонён модератором"

    class Publication(models.TextChoices):
        NONE = "none", "Не опубликован"
        PUBLISHED = "published", "Опубликован"
        CANCELLED = "cancelled", "Отменён"
        FINISHED = "finished", "Завершён"

    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="owned_tournaments", verbose_name="создатель")

    # Рабочая копия (редактирует организатор)
    title = models.CharField("название", max_length=200)
    starts_on = models.DateField("дата начала", null=True, blank=True)
    ends_on = models.DateField("дата окончания", null=True, blank=True)
    venue = models.ForeignKey(Venue, null=True, blank=True, on_delete=models.PROTECT, related_name="tournaments")
    timezone = models.CharField("часовой пояс", max_length=64, blank=True)
    contact_name = models.CharField("контакт для участников: имя", max_length=120, blank=True)
    contact = models.CharField("контакт для участников: телефон", max_length=200, blank=True, help_text="Виден всем на странице турнира (TRN-02)")
    level = models.CharField("статус соревнований", max_length=16, choices=Level.choices, default=Level.CLUB)
    classification = models.BooleanField("классификационные (присвоение разрядов)", default=False)
    entry_deadline = models.DateField("последний день подачи заявок", null=True, blank=True)
    entry_deadline_time = models.TimeField("заявки до (время)", null=True, blank=True)
    cancel_terms = models.TextField("условия отмены", blank=True)
    regulation = models.FileField("положение (PDF)", upload_to="regulations/%Y/%m/", blank=True)

    status = models.CharField("рабочая копия", max_length=16, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField("версия данных", default=1)
    submitted_at = models.DateTimeField(null=True, blank=True)
    moderation_comment = models.TextField("замечания модератора", blank=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Опубликованная версия (видит публика). Меняется только при одобрении модератором.
    publication = models.CharField(max_length=16, choices=Publication.choices, default=Publication.NONE)
    snapshot = models.JSONField("опубликованные сведения", null=True, blank=True)
    snapshot_version = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    published_regulation = models.CharField(max_length=300, blank=True)
    pub_title = models.CharField(max_length=200, blank=True)
    pub_starts_on = models.DateField(null=True, blank=True)
    pub_ends_on = models.DateField(null=True, blank=True)
    pub_region = models.CharField(max_length=16, blank=True)
    pub_disciplines = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    pub_deadline = models.DateField(null=True, blank=True)
    pub_deadline_time = models.TimeField(null=True, blank=True)
    pub_timezone = models.CharField(max_length=64, blank=True)
    cancelled_reason = models.TextField(blank=True)
    min_gap_starts = models.PositiveSmallIntegerField(
        "минимум участников между стартами одной лошади или одного всадника", default=10,
        help_text="0 — не проверять. По умолчанию 10 — для лошадей одного всадника (п. 8.1.1.3 гл. X Правил).",
    )
    finished_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "турнир"
        verbose_name_plural = "турниры"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__isnull=True) | models.Q(starts_on__isnull=True) | models.Q(ends_on__gte=models.F("starts_on")),
                name="tournament_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["publication", "pub_starts_on"])]

    def __str__(self):
        return self.title or f"Турнир #{self.pk}"

    # --- даты ---
    def days(self) -> list[datetime.date]:
        if not self.starts_on:
            return []
        end = self.ends_on or self.starts_on
        return [self.starts_on + datetime.timedelta(days=i) for i in range((end - self.starts_on).days + 1)]

    @property
    def is_multiday(self):
        return bool(self.starts_on and self.ends_on and self.ends_on > self.starts_on)

    @property
    def is_official(self):
        """Официальные соревнования (не клубные) — для подсказок по ограничениям Правил."""
        return self.level in (self.Level.MUNICIPAL, self.Level.REGIONAL, self.Level.NATIONAL, self.Level.INTERNATIONAL)

    @property
    def is_published(self):
        return self.publication != self.Publication.NONE

    @property
    def is_locked(self):
        """Во время проверки рабочую копию менять нельзя."""
        return self.status in (self.Status.REVIEW, self.Status.REJECTED)

    def public_entries_open(self, now=None) -> bool:
        """Открыт ли приём заявок по опубликованной версии (по времени места проведения)."""
        if self.publication != self.Publication.PUBLISHED or not self.pub_deadline or not self.pub_timezone:
            return False
        now = now or timezone.now()
        local = now.astimezone(zoneinfo.ZoneInfo(self.pub_timezone))
        if local.date() != self.pub_deadline:
            return local.date() < self.pub_deadline
        return self.pub_deadline_time is None or local.time().replace(second=0, microsecond=0) <= self.pub_deadline_time

    def mark_changed(self):
        """Правка опубликованного или возвращённого турнира делает рабочую копию черновиком."""
        if self.status in (self.Status.APPROVED, self.Status.RETURNED):
            self.status = self.Status.DRAFT
        self.revision += 1


class ProgramItem(models.Model):
    """Элемент программы: маршрут, езда или другое выступление в конкретный день."""

    class Meta:
        verbose_name = "программа"
        verbose_name_plural = "программа турнира"
        ordering = ["day", "start_time", "position", "id"]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="programs")
    day = models.DateField("день")
    start_time = models.TimeField("начало", null=True, blank=True)
    discipline = models.CharField("дисциплина", max_length=32, choices=DISCIPLINE_CHOICES)
    name = models.CharField("название", max_length=200, help_text="Например: «Маршрут №1 · 80 см»")
    program_type = models.CharField("тип маршрута / езды", max_length=32, help_text="Ключ из apps/sport/jumping.py или dressage_test")
    variant = models.CharField("вариант формулы", max_length=16, blank=True)
    table = models.CharField("таблица штрафов", max_length=6, blank=True)
    heights = ArrayField(models.PositiveSmallIntegerField(), default=list, blank=True, verbose_name="высоты, см")
    dressage_test = models.ForeignKey("sport.DressageTest", null=True, blank=True, on_delete=models.PROTECT, related_name="programs",
                                      verbose_name="езда")
    test_snapshot = models.JSONField("снимок езды", null=True, blank=True)
    arena = models.CharField("манеж", max_length=6, blank=True)
    course = models.JSONField("параметры маршрута", default=dict, blank=True,
                              help_text="Вносятся в день старта: длина, скорость, нормы времени по гитам, число препятствий, хронометраж")
    scheme = models.FileField("схема маршрута", upload_to="schemes/%Y/%m/", blank=True)
    result_fields = models.JSONField("поля результата (прочий маршрут)", default=list, blank=True)
    position = models.PositiveIntegerField(default=0)
    slot_minutes = models.PositiveSmallIntegerField("интервал между стартами, мин", default=2)
    start_times = models.BooleanField("рассчитывать время стартов", default=False,
                                      help_text="Только для выездки. В конкуре время стартов не рассчитывается (RUN-04).")

    def __str__(self):
        return self.name

    @property
    def timed_starts(self):
        """Время старта каждой пары считается только в выездке и только если судья включил расчёт."""
        return self.is_dressage and self.start_times and self.start_time is not None

    @property
    def is_jumping(self):
        return self.discipline == "jumping"

    @property
    def is_dressage(self):
        return self.discipline == "dressage"

    @property
    def route(self):
        from apps.sport.jumping import route

        return route(self.program_type) if self.is_jumping else None

    @property
    def is_manual(self):
        return self.program_type in ("manual", "dressage_manual") or (self.is_dressage and not self.test_snapshot)

    @property
    def program_type_name(self):
        if self.is_jumping:
            return self.route.name
        if self.is_dressage:
            return (self.test_snapshot or {}).get("name") or "Езда (ручной ввод)"
        return PROGRAM_TYPE_NAMES.get(self.program_type, "")

    @property
    def heights_label(self):
        return "-".join(str(h) for h in self.heights) + " см" if self.heights else ""

    @property
    def article_label(self):
        from apps.sport.jumping import article_label

        return article_label(self.route, self.table) if self.is_jumping else ""

    @property
    def discipline_color(self):
        return DISCIPLINE_COLORS.get(self.discipline, "")

    @property
    def result_field_names(self):
        return [RESULT_FIELDS.get(f, f) for f in self.result_fields]


class TournamentClass(models.Model):
    """Зачёт внутри маршрута или езды (TRN-04): условия участия, места считаются отдельно."""

    class RankRule(models.TextChoices):
        NONE = "", "без условия"
        MIN = "min", "не ниже"
        NEVER = "never", "никогда не выполнявшие норматив … и выше"

    class HorseKind(models.TextChoices):
        ANY = "", "лошадь или пони"
        HORSE = "horse", "только лошади"
        PONY = "pony", "только пони"

    program = models.ForeignKey(ProgramItem, on_delete=models.CASCADE, related_name="classes")
    template = models.ForeignKey("sport.ClassTemplate", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField("название зачёта", max_length=200, help_text="Например: «Любители», «Дети 12–14 лет»")
    height = models.PositiveSmallIntegerField("высота, см", null=True, blank=True, help_text="Для маршрута с несколькими высотами")
    birth_year_from = models.PositiveSmallIntegerField("год рождения всадника с", null=True, blank=True)
    birth_year_to = models.PositiveSmallIntegerField("год рождения всадника по", null=True, blank=True)
    rank_rule = models.CharField("условие по разряду", max_length=6, choices=RankRule.choices, blank=True)
    rank = models.CharField("разряд", max_length=8, choices=RANKS, blank=True)
    horse_age_min = models.PositiveSmallIntegerField("возраст лошади от, лет", null=True, blank=True)
    horse_age_max = models.PositiveSmallIntegerField("возраст лошади до, лет", null=True, blank=True)
    horse_kind = models.CharField("лошадь / пони", max_length=6, choices=HorseKind.choices, blank=True)
    height_min = models.PositiveSmallIntegerField("рост в холке от, см", null=True, blank=True)
    height_max = models.PositiveSmallIntegerField("рост в холке до, см", null=True, blank=True)
    min_pairs = models.PositiveSmallIntegerField("минимум пар для открытия", null=True, blank=True)
    note = models.CharField("примечание для участников", max_length=300, blank=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged",
                                    verbose_name="объединён с зачётом")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "зачёт"
        verbose_name_plural = "зачёты"
        ordering = ["height", "position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(birth_year_from__isnull=True) | models.Q(birth_year_to__isnull=True) | models.Q(birth_year_to__gte=models.F("birth_year_from")),
                name="class_birth_years_range",
            ),
            models.CheckConstraint(
                condition=models.Q(horse_age_min__isnull=True) | models.Q(horse_age_max__isnull=True) | models.Q(horse_age_max__gte=models.F("horse_age_min")),
                name="class_horse_age_range",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def label(self):
        return f"{self.name} · {self.height} см" if self.height else self.name

    @property
    def conditions(self) -> str:
        from apps.profiles.models import RANK_SHORT

        parts = []
        a, b = self.birth_year_from, self.birth_year_to
        if a and b:
            parts.append(f"{a}–{b} г.р." if a != b else f"{a} г.р.")
        elif a:
            parts.append(f"{a} г.р. и моложе")
        elif b:
            parts.append(f"{b} г.р. и старше")
        if self.rank_rule == self.RankRule.MIN and self.rank:
            parts.append(f"разряд не ниже {RANK_SHORT.get(self.rank, self.rank)}")
        elif self.rank_rule == self.RankRule.NEVER and self.rank:
            parts.append(f"не выполнявшие норматив {RANK_SHORT.get(self.rank, self.rank)} и выше")
        if self.horse_kind == self.HorseKind.PONY:
            parts.append("пони")
        elif self.horse_kind == self.HorseKind.HORSE:
            parts.append("лошади")
        h1, h2 = self.horse_age_min, self.horse_age_max
        if h1 and h2:
            parts.append(f"лошади {h1} лет" if h1 == h2 else f"лошади {h1}–{h2} лет")
        elif h1:
            parts.append(f"лошади от {h1} лет")
        elif h2:
            parts.append(f"лошади до {h2} лет")
        if self.height_min and self.height_max:
            parts.append(f"рост в холке {self.height_min}–{self.height_max} см")
        elif self.height_min:
            parts.append(f"рост в холке от {self.height_min} см")
        elif self.height_max:
            parts.append(f"рост в холке до {self.height_max} см")
        return ", ".join(parts)


class TeamMember(models.Model):
    """Роль в команде турнира. Выдаётся приглашением по телефону; права появляются после принятия."""

    class Role(models.TextChoices):
        ORGANIZER = "organizer", "Организатор"
        JUDGE = "judge", "Судья"
        STABLE = "stable", "Конюшня и услуги"

    class State(models.TextChoices):
        PENDING = "pending", "Приглашение отправлено"
        ACCEPTED = "accepted", "Принято"
        DECLINED = "declined", "Отклонено"
        REVOKED = "revoked", "Отозвано"

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="team")
    phone = models.CharField("телефон", max_length=16)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="team_roles")
    role = models.CharField("роль", max_length=16, choices=Role.choices)
    title = models.CharField("должность", max_length=80, blank=True, help_text="Главный судья, главный секретарь, курс-дизайнер… Прав не меняет.")
    category = models.CharField("судейская категория", max_length=20, blank=True, help_text="ВК, 1К, 2К, 3К, МК4*…")
    region = models.CharField("регион", max_length=120, blank=True)
    stable_contact = models.BooleanField("ответственный за денники", default=False,
                                         help_text="Его имя и телефон видит участник со своей бронью (решение 27.10.2026). Один на турнир.")
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    invited_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "член команды"
        verbose_name_plural = "команда турнира"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "phone", "role"],
                condition=models.Q(state__in=["pending", "accepted"]),
                name="team_unique_active_role",
            )
        ]

    def __str__(self):
        return f"{self.get_role_display()} {self.phone}"
