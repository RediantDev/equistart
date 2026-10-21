import datetime
import zoneinfo

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from .disciplines import DISCIPLINE_CHOICES, DISCIPLINE_COLORS, PROGRAM_TYPE_NAMES, RESULT_FIELDS
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
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        REVIEW = "review", "На проверке"
        RETURNED = "returned", "Нужны исправления"
        APPROVED = "approved", "Опубликован без изменений"

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
    organizer_name = models.CharField("кто проводит", max_length=200, blank=True)
    contact = models.CharField("контакт организатора", max_length=200, blank=True, help_text="Не публикуется")
    entry_deadline = models.DateField("последний день подачи заявок", null=True, blank=True)
    cancel_terms = models.TextField("условия отмены и возврата", blank=True)
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
    pub_timezone = models.CharField(max_length=64, blank=True)
    cancelled_reason = models.TextField(blank=True)
    min_rest_minutes = models.PositiveSmallIntegerField(
        "минимальный интервал между стартами одной лошади или всадника, мин", default=0,
        help_text="0 — не проверять. Значение задаёт организатор; правила дисциплин — после утверждения матрицы.",
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
    def is_published(self):
        return self.publication != self.Publication.NONE

    @property
    def is_locked(self):
        """Во время проверки рабочую копию менять нельзя."""
        return self.status == self.Status.REVIEW

    def public_entries_open(self, now=None) -> bool:
        """Открыт ли приём заявок по опубликованной версии (по времени места проведения)."""
        if self.publication != self.Publication.PUBLISHED or not self.pub_deadline or not self.pub_timezone:
            return False
        now = now or timezone.now()
        local_today = now.astimezone(zoneinfo.ZoneInfo(self.pub_timezone)).date()
        return local_today <= self.pub_deadline

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
    program_type = models.CharField("вид программы", max_length=32)
    result_fields = models.JSONField("поля результата", default=list)
    limit = models.PositiveIntegerField("лимит участников", null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    slot_minutes = models.PositiveSmallIntegerField("интервал между стартами, мин", default=2)

    def __str__(self):
        return self.name

    @property
    def program_type_name(self):
        return PROGRAM_TYPE_NAMES.get(self.program_type, "")

    @property
    def discipline_color(self):
        return DISCIPLINE_COLORS.get(self.discipline, "")

    @property
    def result_field_names(self):
        return [RESULT_FIELDS.get(f, f) for f in self.result_fields]


class TournamentClass(models.Model):
    """Зачёт внутри программы: условия допуска и стартовый взнос."""

    class Composition(models.TextChoices):
        SINGLE = "single", "Одиночный"
        PAIR = "pair", "Парный"
        GROUP = "group", "Групповой"

    program = models.ForeignKey(ProgramItem, on_delete=models.CASCADE, related_name="classes")
    name = models.CharField("название зачёта", max_length=200, help_text="Например: «Любители», «Дети до 14 лет»")
    composition = models.CharField("состав", max_length=8, choices=Composition.choices, default=Composition.SINGLE)
    min_age = models.PositiveSmallIntegerField("возраст от", null=True, blank=True)
    max_age = models.PositiveSmallIntegerField("возраст до", null=True, blank=True)
    rank_requirement = models.CharField("разряд / звание", max_length=120, blank=True)
    limit = models.PositiveIntegerField("лимит участников", null=True, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "зачёт"
        verbose_name_plural = "зачёты"
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_age__isnull=True) | models.Q(max_age__isnull=True) | models.Q(max_age__gte=models.F("min_age")),
                name="class_age_range",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def conditions(self) -> str:
        parts = []
        if self.min_age and self.max_age:
            parts.append(f"{self.min_age}–{self.max_age} лет")
        elif self.min_age:
            parts.append(f"от {self.min_age} лет")
        elif self.max_age:
            parts.append(f"до {self.max_age} лет")
        if self.rank_requirement:
            parts.append(self.rank_requirement)
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
    phone = models.CharField("телефон", max_length=13)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="team_roles")
    role = models.CharField("роль", max_length=16, choices=Role.choices)
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
