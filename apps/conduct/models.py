from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class StartSlot(models.Model):
    """Место выступления в рабочем стартовом порядке программы."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Ожидает старта"
        STARTED = "started", "На поле"
        FINISHED = "finished", "Завершено"
        WITHDRAWN = "withdrawn", "Снят"
        NO_SHOW = "no_show", "Не явился"

    program = models.ForeignKey("tournaments.ProgramItem", on_delete=models.CASCADE, related_name="slots")
    entry = models.OneToOneField("entries.Entry", on_delete=models.CASCADE, related_name="start_slot")
    position = models.PositiveIntegerField()
    start_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)
    exception_reason = models.CharField("причина исключения из интервала", max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]

    LOCKED = (Status.STARTED, Status.FINISHED)

    @property
    def locked(self):
        return self.status in self.LOCKED


class StartListPublication(models.Model):
    """Опубликованная версия стартового протокола программы. Прежние версии сохраняются."""

    program = models.ForeignKey("tournaments.ProgramItem", on_delete=models.CASCADE, related_name="start_publications")
    version = models.PositiveIntegerField()
    rows = models.JSONField()
    published_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["program", "version"], name="startlist_version_unique")]


class Result(models.Model):
    class Status(models.TextChoices):
        OK = "", "—"
        ELIMINATED = "eliminated", "Выбыл"
        RETIRED = "retired", "Сошёл"
        WITHDRAWN = "withdrawn", "Снят"
        NO_SHOW = "no_show", "Не явился"
        DISQUALIFIED = "disqualified", "Дисквалифицирован"

    entry = models.OneToOneField("entries.Entry", on_delete=models.CASCADE, related_name="result")
    values = models.JSONField(default=dict, blank=True)
    place = models.PositiveIntegerField("место", null=True, blank=True)
    status = models.CharField("статус", max_length=14, choices=Status.choices, blank=True, default="")
    note = models.CharField("примечание", max_length=200, blank=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)


class ResultPublication(models.Model):
    """Опубликованная версия результатов зачёта. Исправление — новая версия, прежние сохраняются."""

    tclass = models.ForeignKey("tournaments.TournamentClass", on_delete=models.CASCADE, related_name="result_publications")
    version = models.PositiveIntegerField()
    rows = models.JSONField()
    fields = models.JSONField(default=list)
    published_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["tclass", "version"], name="result_version_unique")]
