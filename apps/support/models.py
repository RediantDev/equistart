"""Поддержка пользователей (ТЗ 13.2): обращения, переписка, вложения, частые вопросы."""

import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse


def _token():
    return secrets.token_urlsafe(24)


class Topic(models.TextChoices):
    LOGIN = "login", "Вход и пароль"
    DATA = "data", "Ошибка в данных спортсмена или лошади"
    DUPLICATE = "duplicate", "Дубль"
    DISPUTE = "dispute", "Спор о лошади, спортсмене или клубе"
    REVIEW = "review", "Проверка клуба или турнира"
    RESULTS = "results", "Ошибка в результатах"
    BUG = "bug", "Ошибка сервиса"
    OTHER = "other", "Другое"


# SUP-02: от темы зависят поля-ссылки формы
TOPIC_LINKS = {
    Topic.LOGIN: [],
    Topic.DATA: ["athlete", "horse"],
    Topic.DUPLICATE: ["athlete", "horse", "club"],
    Topic.DISPUTE: ["athlete", "horse", "club"],
    Topic.REVIEW: ["tournament", "club"],
    Topic.RESULTS: ["tournament"],
    Topic.BUG: ["tournament"],
    Topic.OTHER: ["tournament", "club"],
}


class Ticket(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новое"
        IN_WORK = "in_work", "В работе"
        WAITING = "waiting", "Ждёт ответа пользователя"
        RESOLVED = "resolved", "Решено"

    author = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="аккаунт", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="support_tickets")
    name = models.CharField("имя", max_length=200, blank=True)
    phone = models.CharField("телефон", max_length=20, blank=True)
    email = models.EmailField("email для ответа", blank=True)
    topic = models.CharField("тема", max_length=20, choices=Topic.choices)
    text = models.TextField("текст обращения", max_length=5000)
    status = models.CharField("статус", max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    urgent = models.BooleanField("идёт турнир", default=False, db_index=True)
    tournament = models.ForeignKey("tournaments.Tournament", verbose_name="турнир", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    athlete = models.ForeignKey("profiles.Athlete", verbose_name="спортсмен", null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    horse = models.ForeignKey("profiles.Horse", verbose_name="лошадь", null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    club = models.ForeignKey("clubs.Club", verbose_name="клуб", null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")
    page = models.CharField("страница", max_length=300, blank=True)
    user_agent = models.CharField("устройство и браузер", max_length=300, blank=True)
    token = models.CharField(max_length=64, unique=True, default=_token, editable=False)
    created_at = models.DateTimeField("создано", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("изменено", auto_now=True)
    last_user_at = models.DateTimeField("последнее сообщение пользователя", null=True, blank=True)
    last_staff_at = models.DateTimeField("последний ответ поддержки", null=True, blank=True)
    transferred_at = models.DateTimeField("передано судьям турнира", null=True, blank=True)

    class Meta:
        verbose_name = "обращение"
        verbose_name_plural = "обращения в поддержку"
        ordering = ["-urgent", "-created_at"]

    def __str__(self):
        return f"№{self.pk} · {self.get_topic_display()}"

    @property
    def contact_name(self):
        return self.author.full_name if self.author_id else self.name

    @property
    def contact_phone(self):
        return self.author.phone_display if self.author_id else self.phone

    @property
    def reply_email(self):
        return self.email or (self.author.email if self.author_id else "")

    def get_absolute_url(self):
        if self.author_id:
            return reverse("support:detail", args=[self.pk])
        return reverse("support:guest", args=[self.token])


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_staff = models.BooleanField("ответ поддержки", default=False)
    text = models.TextField("текст", max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "сообщение"
        verbose_name_plural = "сообщения"
        ordering = ["created_at", "id"]


class TicketAttachment(models.Model):
    """Вложения частные: видят только автор обращения и модератор (SUP-03, ACL-03)."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="support/%Y/%m/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "вложение"
        verbose_name_plural = "вложения"


class Faq(models.Model):
    """Частые вопросы (SUP-12): тексты ведёт модератор в админке."""

    class Section(models.TextChoices):
        GENERAL = "general", "Общие"
        ATHLETE = "athlete", "Спортсмен"
        COACH = "coach", "Тренер / представитель"
        ORGANIZER = "organizer", "Организатор"
        JUDGE = "judge", "Судья"
        CLUB = "club", "Клуб"

    section = models.CharField("раздел", max_length=12, choices=Section.choices, default=Section.GENERAL)
    question = models.CharField("вопрос", max_length=300)
    answer = models.TextField("ответ")
    order = models.PositiveIntegerField("порядок", default=100)
    published = models.BooleanField("опубликован", default=True)

    class Meta:
        verbose_name = "частый вопрос"
        verbose_name_plural = "частые вопросы"
        ordering = ["section", "order", "id"]

    def __str__(self):
        return self.question
