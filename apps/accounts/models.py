from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .phone import PHONE_DB_REGEX, format_phone, normalize_phone


class Category(models.TextChoices):
    """Категории аккаунта, которые пользователь выбирает сам (ТЗ 2.2, раздел 5.1)."""

    ATHLETE = "athlete", "Спортсмен"
    COACH = "coach", "Тренер / представитель"
    ORGANIZER = "organizer", "Организатор"
    JUDGE = "judge", "Судья"
    CLUB = "club", "Клуб"


# Модератор не является самостоятельно выбираемой категорией: назначается администратором.
MODERATOR_MODE = "moderator"

CATEGORY_FIELDS = {
    Category.ATHLETE: "is_athlete",
    Category.COACH: "is_coach",
    Category.ORGANIZER: "is_organizer",
    Category.JUDGE: "is_judge",
    Category.CLUB: "is_club",
}

# Кто может создать турнир (TRN-01): организатор, судья (становится Организатором турнира) или клуб.
CREATOR_CATEGORIES = (Category.ORGANIZER, Category.JUDGE, Category.CLUB)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra):
        phone = normalize_phone(phone)
        user = self.model(phone=phone, **extra)
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra)

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_moderator", True)
        extra.setdefault("last_name", "Администратор")
        extra.setdefault("first_name", "EquiStart")
        if not extra.get("is_staff") or not extra.get("is_superuser"):
            raise ValueError("Суперпользователь должен иметь is_staff и is_superuser")
        return self._create_user(phone, password, **extra)

    def get_by_natural_key(self, username):
        try:
            username = normalize_phone(username)
        except ValidationError:
            pass
        return self.get(**{self.model.USERNAME_FIELD: username})


class User(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(
        "телефон",
        max_length=16,
        unique=True,
        help_text="Логин. Мобильный номер в международном формате: +7…, +375…, +998… и т. п.",
    )
    phone_verified_at = models.DateTimeField("телефон подтверждён", null=True, blank=True)

    last_name = models.CharField("фамилия", max_length=100)
    first_name = models.CharField("имя", max_length=100)
    middle_name = models.CharField("отчество", max_length=100, blank=True)
    email = models.EmailField("email для связи", blank=True)

    is_athlete = models.BooleanField("спортсмен", default=False)
    is_coach = models.BooleanField("тренер / представитель", default=False)
    is_organizer = models.BooleanField("организатор", default=False)
    is_judge = models.BooleanField("судья", default=False)
    is_club = models.BooleanField("клуб", default=False)
    is_moderator = models.BooleanField(
        "модератор",
        default=False,
        help_text="Назначается только администратором. Даёт доступ к проверке турниров и обращений.",
    )

    pd_consent_at = models.DateTimeField("согласие на обработку ПДн", null=True, blank=True)
    pd_consent_version = models.CharField("версия согласия", max_length=20, blank=True)

    is_active = models.BooleanField("активен", default=True)
    is_staff = models.BooleanField("доступ к панели управления", default=False)
    date_joined = models.DateTimeField("зарегистрирован", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(phone__regex=PHONE_DB_REGEX),
                name="user_phone_normalized",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.phone_display})"

    def clean(self):
        super().clean()
        if self.phone:
            self.phone = normalize_phone(self.phone)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.last_name, self.first_name, self.middle_name) if p)

    @property
    def short_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def phone_display(self) -> str:
        return format_phone(self.phone)

    @property
    def phone_verified(self) -> bool:
        return self.phone_verified_at is not None

    def categories(self) -> list[str]:
        return [cat for cat, field in CATEGORY_FIELDS.items() if getattr(self, field)]

    def category_labels(self) -> list[str]:
        labels = [cat.label for cat in Category if getattr(self, CATEGORY_FIELDS[cat])]
        if self.is_moderator:
            labels.append("Модератор")
        return labels

    def missing_categories(self) -> list[Category]:
        return [cat for cat in Category if not getattr(self, CATEGORY_FIELDS[cat])]

    @property
    def can_create_tournament(self) -> bool:
        return any(getattr(self, CATEGORY_FIELDS[c]) for c in CREATOR_CATEGORIES)

    def add_category(self, category: str) -> None:
        """Добавляет самостоятельно выбираемую категорию. Модератора добавить нельзя."""
        cat = Category(category)  # ValueError для неизвестной категории, включая moderator
        setattr(self, CATEGORY_FIELDS[cat], True)


class PhoneCode(models.Model):
    """Код подтверждения телефона. Хранится только хеш."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="phone_codes")
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
