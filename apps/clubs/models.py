"""Профили клубов (ТЗ 8.4, CLB-01…06): публичная страница, подтверждение модератором, новости, связь с местом проведения."""

from django.conf import settings
from django.db import models

from apps.tournaments.regions import REGION_CHOICES, REGION_NAMES

User = settings.AUTH_USER_MODEL


class Club(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        REVIEW = "review", "На проверке"
        RETURNED = "returned", "Нужны исправления"
        CONFIRMED = "confirmed", "Подтверждён"

    manager = models.ForeignKey(User, on_delete=models.PROTECT, related_name="managed_clubs", verbose_name="управляющий")
    name = models.CharField("название", max_length=200)
    description = models.TextField("описание", blank=True)
    region = models.CharField("регион", max_length=16, choices=REGION_CHOICES)
    settlement = models.CharField("населённый пункт", max_length=120)
    address = models.CharField("адрес", max_length=300)
    phone = models.CharField("телефон", max_length=40, blank=True)
    email = models.EmailField("email", blank=True)
    site = models.URLField("сайт", blank=True)
    socials = models.CharField("соцсети", max_length=300, blank=True, help_text="Ссылки через пробел: VK, Telegram, Instagram и т. п.")
    logo = models.FileField("логотип", upload_to="clubs/logos/%Y/", blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    moderator_comment = models.TextField("замечания модератора", blank=True)
    # опубликованная версия существенных полей: пока правка названия/адреса на проверке, публика видит прежние
    pub_name = models.CharField(max_length=200, blank=True)
    pub_settlement = models.CharField(max_length=120, blank=True)
    pub_address = models.CharField(max_length=300, blank=True)
    pub_region = models.CharField(max_length=16, blank=True)
    venue = models.OneToOneField("tournaments.Venue", null=True, blank=True, on_delete=models.SET_NULL, related_name="club",
                                 verbose_name="место проведения")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged",
                                    verbose_name="объединён с")
    merged_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "клуб"
        verbose_name_plural = "клубы"
        ordering = ["name"]

    def __str__(self):
        return self.public_name or self.name

    @property
    def is_public(self):
        return bool(self.confirmed_at) and not self.merged_into_id

    @property
    def public_name(self):
        return self.pub_name or self.name

    @property
    def public_settlement(self):
        return self.pub_settlement or self.settlement

    @property
    def public_address(self):
        return self.pub_address or self.address

    @property
    def public_region_name(self):
        return REGION_NAMES.get(self.pub_region or self.region, "")

    @property
    def region_name(self):
        return REGION_NAMES.get(self.region, "")

    def substantial_changed(self):
        return self.is_public and (self.name, self.address, self.settlement, self.region) != (
            self.pub_name, self.pub_address, self.pub_settlement, self.pub_region)

    def social_links(self):
        return [s for s in (self.socials or "").split() if s.startswith("http")]


class ClubPhoto(models.Model):
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="photos")
    file = models.FileField(upload_to="clubs/photos/%Y/")
    caption = models.CharField("подпись", max_length=200, blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]


class ClubNews(models.Model):
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="news")
    title = models.CharField("заголовок", max_length=200)
    text = models.TextField("текст")
    tournament = models.ForeignKey("tournaments.Tournament", null=True, blank=True, on_delete=models.SET_NULL, related_name="club_news",
                                   verbose_name="турнир")
    published_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    hidden = models.BooleanField("скрыта модератором", default=False)
    hidden_reason = models.CharField("причина скрытия", max_length=300, blank=True)

    class Meta:
        ordering = ["-published_at", "-id"]
        verbose_name = "новость клуба"
        verbose_name_plural = "новости клубов"


class NewsPhoto(models.Model):
    news = models.ForeignKey(ClubNews, on_delete=models.CASCADE, related_name="photos")
    file = models.FileField(upload_to="clubs/news/%Y/")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class ClubMember(models.Model):
    """Управляющие клубом кроме владельца (владелец — Club.manager). Приглашение по телефону, принимается в кабинете."""

    class State(models.TextChoices):
        PENDING = "pending", "Ждёт ответа"
        ACCEPTED = "accepted", "Управляет"
        DECLINED = "declined", "Отклонено"

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="members")
    phone = models.CharField(max_length=20)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="club_memberships")
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    invited_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(fields=["club", "phone"], name="club_member_unique_phone")]
