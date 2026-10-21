import re
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL

RANKS = [
    ("none", "б/р"),
    ("3y", "III юношеский"),
    ("2y", "II юношеский"),
    ("1y", "I юношеский"),
    ("3", "III разряд"),
    ("2", "II разряд"),
    ("1", "I разряд"),
    ("kms", "КМС"),
    ("ms", "МС"),
    ("msmk", "МСМК"),
]
RANK_SHORT = {"none": "б/р", "3y": "III юн.", "2y": "II юн.", "1y": "I юн.", "3": "III", "2": "II", "1": "I", "kms": "КМС", "ms": "МС", "msmk": "МСМК"}
# Шкала ЕВСК по возрастанию: для условий зачёта «не ниже …» и «не выполнявшие … и выше».
RANK_ORDER = {code: i for i, (code, _) in enumerate(RANKS)}
DISCIPLINE_RANK_FIELD = {"jumping": "rank_jumping", "dressage": "rank_dressage"}


def rank_at_least(rank: str, minimum: str) -> bool:
    return RANK_ORDER.get(rank or "none", 0) >= RANK_ORDER.get(minimum, 0)

COLORS = [
    ("bay", "гнедая"), ("dark_bay", "тёмно-гнедая"), ("light_bay", "светло-гнедая"), ("chestnut", "рыжая"),
    ("black", "вороная"), ("grey", "серая"), ("brown", "караковая"), ("buckskin", "буланая"),
    ("bay_dun", "саврасая"), ("palomino", "соловая"), ("cremello", "изабелловая"), ("roan", "чалая"),
    ("pinto", "пегая"), ("appaloosa", "чубарая"), ("other", "другая"),
]
COLOR_SHORT = dict(COLORS)
SEXES = [("stallion", "жеребец"), ("mare", "кобыла"), ("gelding", "мерин")]
SEX_SHORT = {"stallion": "жер.", "mare": "коб.", "gelding": "мер."}


def normalize_fksr(value: str) -> str:
    return re.sub(r"[\s\-]", "", (value or "")).upper()


def new_horse_code() -> str:
    alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ23456789"
    return "EQ-" + "".join(secrets.choice(alphabet) for _ in range(6))


class Athlete(models.Model):
    """Запись спортсмена. Аккаунт и телефон не обязательны. Ровно один управляющий."""

    last_name = models.CharField("фамилия", max_length=100)
    first_name = models.CharField("имя", max_length=100)
    middle_name = models.CharField("отчество", max_length=100, blank=True)
    birth_date = models.DateField("дата рождения", null=True, blank=True)
    rank_jumping = models.CharField("разряд / звание — конкур", max_length=8, choices=RANKS, blank=True)
    rank_dressage = models.CharField("разряд / звание — выездка", max_length=8, choices=RANKS, blank=True)
    fksr_number = models.CharField("номер ФКСР", max_length=40, blank=True)
    fei_id = models.CharField("FEI ID", max_length=20, blank=True)
    club = models.CharField("клуб", max_length=200, blank=True)
    region = models.CharField("регион", max_length=120, blank=True)
    coach_name = models.CharField("тренер (ФИО)", max_length=200, blank=True, help_text="Только подпись в протоколе, прав доступа не даёт.")

    manager = models.ForeignKey(User, on_delete=models.PROTECT, related_name="managed_athletes", verbose_name="управляющий")
    linked_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="athlete_records", verbose_name="аккаунт спортсмена"
    )
    consent_confirmed_at = models.DateTimeField("подтверждено согласие на обработку и публикацию", null=True, blank=True)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "спортсмен"
        verbose_name_plural = "спортсмены"
        ordering = ["last_name", "first_name"]
        # Дублей нет: номер ФКСР и FEI ID уникальны среди действующих записей (решение заказчика 14.10.2026).
        constraints = [
            models.UniqueConstraint(fields=["fksr_number"], condition=~models.Q(fksr_number="") & models.Q(archived=False), name="athlete_fksr_unique_active"),
            models.UniqueConstraint(fields=["fei_id"], condition=~models.Q(fei_id="") & models.Q(archived=False), name="athlete_fei_unique_active"),
        ]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.fksr_number = normalize_fksr(self.fksr_number)
        self.fei_id = normalize_fksr(self.fei_id)
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return " ".join(p for p in (self.last_name, self.first_name, self.middle_name) if p)

    @property
    def public_name(self):
        return f"{self.last_name} {self.first_name}"

    @property
    def birth_year(self):
        return self.birth_date.year if self.birth_date else None

    def rank_for(self, discipline: str | None) -> str:
        """Код разряда по дисциплине (конкур или выездка); для прочих — лучший из двух."""
        field = DISCIPLINE_RANK_FIELD.get(discipline or "")
        if field:
            return getattr(self, field) or ""
        return max((self.rank_jumping, self.rank_dressage), key=lambda r: RANK_ORDER.get(r or "none", 0)) or ""

    def rank_short_for(self, discipline: str | None) -> str:
        return RANK_SHORT.get(self.rank_for(discipline), "")

    @property
    def rank_short(self):
        parts = []
        if self.rank_jumping:
            parts.append(f"конкур {RANK_SHORT[self.rank_jumping]}")
        if self.rank_dressage:
            parts.append(f"выездка {RANK_SHORT[self.rank_dressage]}")
        return ", ".join(parts)

    def summary(self):
        parts = [str(self.birth_year) if self.birth_year else "", self.rank_short, self.club]
        return " · ".join(p for p in parts if p)

    def public_snapshot(self, discipline: str | None = None):
        """Сведения для протоколов: без отчества, полной даты рождения и контактов. Разряд — по дисциплине."""
        return {"name": self.public_name, "birth_year": self.birth_year, "rank": self.rank_short_for(discipline), "club": self.club,
                "region": self.region, "coach": self.coach_name}

    def private_snapshot(self, discipline: str | None = None):
        d = self.public_snapshot(discipline)
        d.update({"full_name": self.full_name, "birth_date": self.birth_date.isoformat() if self.birth_date else None, "fksr": self.fksr_number, "fei": self.fei_id})
        return d


class AthleteDelegate(models.Model):
    """Разрешение тренеру/менеджеру подавать заявки за спортсмена (без права редактировать профиль)."""

    athlete = models.ForeignKey(Athlete, on_delete=models.CASCADE, related_name="delegates")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="delegated_athletes")
    granted_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["athlete", "user"], name="athlete_delegate_unique")]


class Horse(models.Model):
    name = models.CharField("кличка", max_length=100)
    birth_year = models.PositiveSmallIntegerField("год рождения")
    color = models.CharField("масть", max_length=16, choices=COLORS)
    sex = models.CharField("пол", max_length=10, choices=SEXES)
    breed = models.CharField("порода", max_length=100)
    fksr_number = models.CharField("номер ФКСР", max_length=40, blank=True)
    fei_id = models.CharField("FEI ID / паспорт FEI", max_length=20, blank=True)
    code = models.CharField("внутренний код", max_length=12, unique=True, default=new_horse_code, editable=False)
    sire = models.CharField("отец", max_length=100, blank=True)
    birthplace = models.CharField("место рождения", max_length=200, blank=True)
    owner_name = models.CharField("владелец", max_length=200, blank=True, help_text="Не публикуется. Не является проверкой права собственности.")
    is_pony = models.BooleanField("пони (рост в холке до 150 см)", default=False)
    height_cm = models.PositiveSmallIntegerField("рост в холке, см", null=True, blank=True, help_text="Нужен для зачётов с условием по росту (пони).")
    manager = models.ForeignKey(User, on_delete=models.PROTECT, related_name="managed_horses", verbose_name="управляющий")
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "лошадь"
        verbose_name_plural = "лошади"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["fksr_number"], condition=~models.Q(fksr_number="") & models.Q(archived=False), name="horse_fksr_unique_active"
            ),
            models.UniqueConstraint(fields=["fei_id"], condition=~models.Q(fei_id="") & models.Q(archived=False), name="horse_fei_unique_active"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.fksr_number = normalize_fksr(self.fksr_number)
        self.fei_id = normalize_fksr(self.fei_id)
        super().save(*args, **kwargs)

    def summary(self):
        return " · ".join(p for p in (str(self.birth_year), SEX_SHORT.get(self.sex, ""), COLOR_SHORT.get(self.color, ""), self.breed) if p)

    def public_snapshot(self):
        return {
            "name": self.name, "birth_year": self.birth_year, "sex": SEX_SHORT.get(self.sex, ""),
            "color": COLOR_SHORT.get(self.color, ""), "breed": self.breed, "code": self.code,
        }


class HorseDocument(models.Model):
    class Kind(models.TextChoices):
        PASSPORT = "passport", "Ветеринарный паспорт"
        VACCINATION = "vaccination", "Прививки и исследования"
        OTHER = "other", "Другой документ"

    horse = models.ForeignKey(Horse, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField("тип", max_length=16, choices=Kind.choices)
    title = models.CharField("название", max_length=200, blank=True)
    file = models.FileField(upload_to="horse_docs/%Y/%m/")
    content_type = models.CharField(max_length=60)
    valid_until = models.DateField("действует до", null=True, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    @property
    def label(self):
        return self.title or self.get_kind_display()

    @property
    def expired(self):
        return bool(self.valid_until and self.valid_until < timezone.localdate())


class Offer(models.Model):
    """Предложение по телефону: связать запись спортсмена с аккаунтом или передать управление."""

    class Kind(models.TextChoices):
        ATHLETE_LINK = "athlete_link", "Связать с аккаунтом спортсмена"
        ATHLETE_TRANSFER = "athlete_transfer", "Передать управление спортсменом"
        HORSE_TRANSFER = "horse_transfer", "Передать управление лошадью"

    class State(models.TextChoices):
        PENDING = "pending", "Ожидает"
        ACCEPTED = "accepted", "Принято"
        DECLINED = "declined", "Отклонено"
        CANCELLED = "cancelled", "Отменено"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    athlete = models.ForeignKey(Athlete, null=True, blank=True, on_delete=models.CASCADE, related_name="offers")
    horse = models.ForeignKey(Horse, null=True, blank=True, on_delete=models.CASCADE, related_name="offers")
    phone = models.CharField(max_length=13)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "athlete", "phone"], condition=models.Q(state="pending"), name="offer_athlete_pending_unique"),
            models.UniqueConstraint(fields=["kind", "horse", "phone"], condition=models.Q(state="pending"), name="offer_horse_pending_unique"),
        ]

    @property
    def subject(self):
        return self.athlete or self.horse
