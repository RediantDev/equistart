from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class Application(models.Model):
    """Заявка — контейнер независимых позиций (выступлений). Индивидуальная или групповая."""

    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.PROTECT, related_name="applications")
    submitted_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="applications", verbose_name="подал")
    contact_phone = models.CharField("телефон для связи", max_length=13, blank=True)
    contact_name = models.CharField("контактное лицо", max_length=200, blank=True)
    comment = models.TextField("комментарий", blank=True)
    late = models.BooleanField("внесена судьёй (поздняя / на месте)", default=False)
    idempotency_key = models.CharField(max_length=64, unique=True)
    content_hash = models.CharField(max_length=64)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "заявка"
        verbose_name_plural = "заявки"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Заявка №{self.pk}"

    @property
    def number(self):
        return self.pk

    @property
    def is_group(self):
        return self.entries.values("athlete").distinct().count() > 1


class Entry(models.Model):
    """Выступление: спортсмен + лошадь + программа + ровно один зачёт. Один статус выступления; бронь и услуги — отдельно."""

    class Decision(models.TextChoices):
        PENDING = "pending", "На рассмотрении"
        ACCEPTED = "accepted", "Принято"
        REJECTED = "rejected", "Отклонено"
        CANCELLED = "cancelled", "Отменено"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="entries")
    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.PROTECT, related_name="entries")
    program = models.ForeignKey("tournaments.ProgramItem", on_delete=models.PROTECT, related_name="entries")
    tclass = models.ForeignKey("tournaments.TournamentClass", on_delete=models.PROTECT, related_name="entries")
    athlete = models.ForeignKey("profiles.Athlete", on_delete=models.PROTECT, related_name="entries")
    horse = models.ForeignKey("profiles.Horse", on_delete=models.PROTECT, related_name="entries")

    # Снимки на момент подачи (правка профиля их не переписывает)
    athlete_snapshot = models.JSONField(default=dict)
    horse_snapshot = models.JSONField(default=dict)
    program_name = models.CharField(max_length=200)
    class_name = models.CharField(max_length=200)
    day = models.DateField()
    height = models.PositiveSmallIntegerField("высота, см", null=True, blank=True)
    conditions_confirmed = models.BooleanField("участник подтвердил условие зачёта", default=False)

    decision = models.CharField(max_length=12, choices=Decision.choices, default=Decision.PENDING)
    decision_reason = models.TextField("причина (не публикуется)", blank=True)
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)

    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "выступление"
        verbose_name_plural = "выступления"
        ordering = ["day", "program__start_time", "program_id", "id"]
        indexes = [models.Index(fields=["tournament", "decision"]), models.Index(fields=["tclass", "decision"])]

    def __str__(self):
        return f"{self.athlete_snapshot.get('name')} / {self.horse_snapshot.get('name')} · {self.program_name}"

    ACTIVE = (Decision.PENDING, Decision.ACCEPTED)

    @property
    def is_active(self):
        return self.decision in self.ACTIVE

    @property
    def is_started(self):
        """Начатое или завершённое выступление нельзя менять и отменять (заполняется этапом проведения)."""
        slot = getattr(self, "start_slot", None)
        return bool(slot and slot.status in ("started", "finished"))


class ChangeProposal(models.Model):
    """Предложение замены спортсмена, лошади или зачёта. До решения действуют прежние сведения."""

    class State(models.TextChoices):
        PENDING = "pending", "Ожидает решения"
        ACCEPTED = "accepted", "Принято"
        REJECTED = "rejected", "Отклонено"
        WITHDRAWN = "withdrawn", "Отозвано"

    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="proposals")
    athlete = models.ForeignKey("profiles.Athlete", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    horse = models.ForeignKey("profiles.Horse", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    tclass = models.ForeignKey("tournaments.TournamentClass", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["entry"], condition=models.Q(state="pending"), name="one_pending_proposal_per_entry")]

    def describe(self):
        parts = []
        if self.athlete_id:
            parts.append(f"спортсмен → {self.athlete.public_name}")
        if self.horse_id:
            parts.append(f"лошадь → {self.horse.name}")
        if self.tclass_id:
            parts.append(f"зачёт → {self.tclass.program.name} · {self.tclass.name}")
        return "; ".join(parts)


class ApplicationDraft(models.Model):
    """Черновик заявки: собранные пары и выступления хранятся в базе, а не в сессии —
    не пропадают при выходе и доступны с другого устройства (отчёт тестирования 22.10.2026)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="application_drafts")
    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.CASCADE, related_name="drafts")
    late = models.BooleanField(default=False)
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "черновик заявки"
        verbose_name_plural = "черновики заявок"
        constraints = [models.UniqueConstraint(fields=["user", "tournament", "late"], name="one_draft_per_tournament")]
