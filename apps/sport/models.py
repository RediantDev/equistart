"""Справочники дисциплин (ТЗ 2.2, раздел 8.5): езды выездки (DIR-02) и шаблоны зачётов (DIR-03).

Типы маршрутов конкура (DIR-01) — в `jumping.py`: каждый тип связан с формулой подсчёта в коде,
поэтому новый тип добавляется вместе с кодом, а не в админке.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.profiles.models import RANKS

User = settings.AUTH_USER_MODEL


class DressageTest(models.Model):
    """Езда = шаблон судейского листа.

    `parts` — части листа, у каждой свой максимум и свои судьи:
        [{"key": "technique", "title": "Техника", "step": 0.5,
          "movements": [{"n": 1, "letters": "A X", "text": "Въезд…", "criteria": "…", "coef": 1}],
          "collective": [{"text": "Гармония", "coef": 2}]}]
    Обычная езда — одна часть; детская — «Техника» и «Качество езды»; КЮР — «Техника» и «Артистизм».
    """

    class Kind(models.TextChoices):
        STANDARD = "standard", "Обычная езда"
        CHILDREN = "children", "Детская: техника + качество езды"
        FREESTYLE = "freestyle", "КЮР: техника + артистизм"

    class Arena(models.TextChoices):
        A20X40 = "20x40", "20 × 40 м"
        A20X60 = "20x60", "20 × 60 м"

    class TieRule(models.TextChoices):
        COLLECTIVE = "collective", "по сумме общих оценок"
        ARTISTIC = "artistic", "по артистизму (КЮР)"
        SHARE = "share", "места делятся"

    name = models.CharField("название", max_length=200)
    short = models.CharField("сокращение", max_length=30, blank=True, help_text="ПП, КП, МП, СП1…")
    level = models.CharField("уровень / возрастная группа", max_length=120, blank=True)
    kind = models.CharField("тип подсчёта", max_length=12, choices=Kind.choices, default=Kind.STANDARD)
    arena = models.CharField("манеж", max_length=6, choices=Arena.choices, default=Arena.A20X60)
    duration_min = models.DecimalField("время езды, мин", max_digits=4, decimal_places=1, null=True, blank=True)
    parts = models.JSONField("части листа", default=list)
    penalty_scheme = models.CharField("схема штрафов", max_length=16, blank=True,
                                      help_text="Выбирается при создании езды; значения по умолчанию нет (Q7).")
    tie_rule = models.CharField("правило при равенстве", max_length=12, choices=TieRule.choices, blank=True)
    min_horse_age = models.PositiveSmallIntegerField("минимальный возраст лошади", null=True, blank=True)
    source = models.CharField("источник", max_length=200, blank=True, help_text="Например: бланк ФКСР, Регламент для пони, приложение 3")
    is_official = models.BooleanField("официальная (вносит модератор)", default=False)
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="dressage_tests",
                              help_text="Клубная езда видна только её автору.")
    is_active = models.BooleanField("доступна для выбора", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "езда (выездка)"
        verbose_name_plural = "езды (выездка)"
        ordering = ["-is_official", "name"]

    def __str__(self):
        return self.name + ("" if self.is_official else " (клубная)")

    def clean(self):
        from . import dressage

        try:
            dressage.validate_parts(self.kind, self.parts)
        except ValueError as e:
            raise ValidationError({"parts": str(e)})
        if self.penalty_scheme and self.penalty_scheme not in dressage.PENALTY_SCHEMES:
            raise ValidationError({"penalty_scheme": "Неизвестная схема штрафов."})

    @property
    def max_points(self) -> dict:
        from . import dressage

        return {p["key"]: dressage.part_max(p) for p in self.parts}

    def snapshot(self) -> dict:
        """Копия езды для турнира: правка справочника не меняет уже созданные турниры."""
        return {
            "id": self.pk, "name": self.name, "short": self.short, "level": self.level, "kind": self.kind,
            "arena": self.arena, "duration_min": float(self.duration_min) if self.duration_min else None,
            "parts": self.parts, "penalty_scheme": self.penalty_scheme, "tie_rule": self.tie_rule,
            "min_horse_age": self.min_horse_age, "is_official": self.is_official, "source": self.source,
        }


class ClassTemplate(models.Model):
    """Типовой зачёт (DIR-03). Возраст — в годах, при выборе пересчитывается в годы рождения от года турнира."""

    class RankRule(models.TextChoices):
        NONE = "", "без условия"
        MIN = "min", "не ниже"
        NEVER = "never", "никогда не выполнявшие норматив … и выше"

    class HorseKind(models.TextChoices):
        ANY = "", "лошадь или пони"
        HORSE = "horse", "только лошади"
        PONY = "pony", "только пони"

    name = models.CharField("название", max_length=120)
    disciplines = models.CharField("дисциплины", max_length=40, default="jumping,dressage",
                                   help_text="Через запятую: jumping, dressage")
    age_from = models.PositiveSmallIntegerField("возраст всадника от, лет", null=True, blank=True)
    age_to = models.PositiveSmallIntegerField("возраст всадника до, лет", null=True, blank=True)
    rank_rule = models.CharField("условие по разряду", max_length=6, choices=RankRule.choices, blank=True)
    rank = models.CharField("разряд", max_length=8, choices=RANKS, blank=True)
    horse_age_min = models.PositiveSmallIntegerField("возраст лошади от, лет", null=True, blank=True)
    horse_age_max = models.PositiveSmallIntegerField("возраст лошади до, лет", null=True, blank=True)
    horse_kind = models.CharField("лошадь / пони", max_length=6, choices=HorseKind.choices, blank=True)
    height_min = models.PositiveSmallIntegerField("рост в холке от, см", null=True, blank=True)
    height_max = models.PositiveSmallIntegerField("рост в холке до, см", null=True, blank=True)
    min_pairs = models.PositiveSmallIntegerField("минимум пар для открытия", null=True, blank=True)
    note = models.CharField("примечание", max_length=300, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField("показывать", default=True)

    class Meta:
        verbose_name = "шаблон зачёта"
        verbose_name_plural = "шаблоны зачётов"
        ordering = ["position", "name"]

    def __str__(self):
        return self.name

    def for_year(self, year: int) -> dict:
        """Поля зачёта для турнира данного года: возраст → годы рождения."""
        return {
            "name": self.name,
            "birth_year_from": year - self.age_to if self.age_to is not None else None,
            "birth_year_to": year - self.age_from if self.age_from is not None else None,
            "rank_rule": self.rank_rule, "rank": self.rank,
            "horse_age_min": self.horse_age_min, "horse_age_max": self.horse_age_max,
            "horse_kind": self.horse_kind, "height_min": self.height_min, "height_max": self.height_max,
            "min_pairs": self.min_pairs, "note": self.note,
        }
