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
    """Итог выступления. Для маршрутов JMP-02 и езд выездки считается автоматически (RES-01); для «прочего» — вводит судья."""

    class Status(models.TextChoices):
        OK = "", "—"
        ELIMINATED = "eliminated", "Исключён"
        RETIRED = "retired", "Сошёл"
        WITHDRAWN = "withdrawn", "Снят"
        NO_SHOW = "no_show", "Не стартовал"
        DISQUALIFIED = "disqualified", "Дисквалифицирован"

    entry = models.OneToOneField("entries.Entry", on_delete=models.CASCADE, related_name="result")
    values = models.JSONField(default=dict, blank=True)
    place = models.PositiveIntegerField("место", null=True, blank=True)
    status = models.CharField("статус", max_length=14, choices=Status.choices, blank=True, default="")
    note = models.CharField("примечание", max_length=200, blank=True)
    auto = models.BooleanField("посчитано автоматически", default=False)
    sort_key = models.JSONField(default=list, blank=True)
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


class JumpRound(models.Model):
    """Гит / фаза / перепрыжка конкура: что ввёл судья у поля (JMP-03). Итог считает apps/sport/jumping.py."""

    class Status(models.TextChoices):
        OK = "ok", "Закончил"
        ELIMINATED = "eliminated", "Исключён"
        RETIRED = "retired", "Сошёл"
        WITHDRAWN = "withdrawn", "Снят"
        NO_SHOW = "no_show", "Не стартовал"

    entry = models.ForeignKey("entries.Entry", on_delete=models.CASCADE, related_name="jump_rounds")
    round = models.CharField("гит", max_length=8)  # main, jo1, jo2, phase1, phase2, round1, round2
    knockdowns = models.PositiveSmallIntegerField("повалы", default=0)
    refusals = models.PositiveSmallIntegerField("неповиновения", default=0)
    corrections = models.PositiveSmallIntegerField("коррекции +6 с", default=0)
    time = models.DecimalField("время, с", max_digits=7, decimal_places=2, null=True, blank=True)
    fall = models.BooleanField("падение", default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OK)
    reason = models.CharField("причина исключения", max_length=200, blank=True)
    revision = models.PositiveIntegerField(default=1)
    client_key = models.CharField(max_length=64, blank=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["entry_id", "id"]
        constraints = [models.UniqueConstraint(fields=["entry", "round"], name="jumpround_unique")]


class JudgePosition(models.Model):
    """Судейская бригада езды (DRS-02): 3 или 5 судей на буквах и судья-подсчёт."""

    class Letter(models.TextChoices):
        C = "C", "C"
        E = "E", "E"
        B = "B", "B"
        H = "H", "H"
        M = "M", "M"
        SCORER = "SCORER", "Судья-подсчёт"

    class Role(models.TextChoices):
        ALL = "all", "Весь лист"
        TECHNIQUE = "technique", "Техника"
        QUALITY = "quality", "Качество езды"

    program = models.ForeignKey("tournaments.ProgramItem", on_delete=models.CASCADE, related_name="judge_positions")
    letter = models.CharField("позиция", max_length=6, choices=Letter.choices)
    role = models.CharField("что оценивает", max_length=10, choices=Role.choices, default=Role.ALL)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="judge_positions", verbose_name="кто вводит оценки")
    judge_name = models.CharField("судья (для протокола)", max_length=200)
    judge_category = models.CharField("категория", max_length=20, blank=True)
    judge_region = models.CharField("регион", max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["program_id", "id"]
        constraints = [models.UniqueConstraint(fields=["program", "letter"], name="judgeposition_unique_letter")]

    @property
    def is_scorer(self):
        return self.letter == self.Letter.SCORER

    @property
    def caption(self):
        extra = ", ".join(x for x in (self.judge_category, self.judge_region) if x)
        return f"{self.letter} — {self.judge_name}" + (f" ({extra})" if extra else "")


class DressageSheet(models.Model):
    """Лист судьи на букве по одной паре (DRS-03/04). После подписи закрыт; правка — только после возврата судьёй-подсчётом."""

    entry = models.ForeignKey("entries.Entry", on_delete=models.CASCADE, related_name="dressage_sheets")
    position = models.ForeignKey(JudgePosition, on_delete=models.PROTECT, related_name="sheets")
    marks = models.JSONField("оценки", default=dict, blank=True)  # {part_key: [оценка | null, …]}
    comments = models.JSONField("комментарии", default=dict, blank=True)  # {"part:index": "текст"}
    remark = models.TextField("общее замечание", blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    returned_reason = models.CharField("причина возврата", max_length=300, blank=True)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entry", "position"], name="dressagesheet_unique")]

    @property
    def signed(self):
        return self.signed_at is not None


class DressageFlags(models.Model):
    """Отметки судьи на C по паре (DRS-05): ошибки в схеме, технические нарушения, исключение; утверждение судьёй-подсчётом."""

    entry = models.OneToOneField("entries.Entry", on_delete=models.CASCADE, related_name="dressage_flags")
    errors = models.PositiveSmallIntegerField("ошибки в схеме", default=0)
    technical = models.PositiveSmallIntegerField("технические нарушения", default=0)
    eliminated = models.BooleanField("исключение", default=False)
    reason = models.CharField("причина исключения", max_length=200, blank=True)
    norm = models.CharField("выполненный норматив", max_length=20, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)
