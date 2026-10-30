from django.conf import settings
from django.db import models


ACTION_LABELS = {
    "tournament.create": "Создан черновик",
    "tournament.edit": "Изменены сведения",
    "tournament.submit": "Отправлен на проверку",
    "tournament.withdraw": "Снят с проверки для правок",
    "tournament.approve": "Опубликован модератором",
    "tournament.return": "Возвращён с замечаниями",
    "tournament.reject": "Отклонён модератором",
    "tournament.cancel": "Соревнование отменено",
    "tournament.delete": "Черновик удалён",
    "program.save": "Сохранена программа",
    "program.delete": "Удалена программа",
    "class.save": "Сохранён зачёт",
    "class.delete": "Удалён зачёт",
    "team.invite": "Отправлено приглашение в команду",
    "team.accept": "Приглашение принято",
    "team.decline": "Приглашение отклонено",
    "team.revoke": "Доступ отозван",
    "venue.confirm": "Место проведения подтверждено",
    "support.create": "Обращение создано",
    "support.user_reply": "Ответ пользователя",
    "support.staff_reply": "Ответ поддержки",
    "support.status": "Изменён статус обращения",
    "support.transfer": "Передано команде турнира",
    "user.block": "Аккаунт заблокирован",
    "user.unblock": "Аккаунт разблокирован",
    "user.password_reset": "Пароль сброшен администратором",
    "user.delete": "Аккаунт удалён",
    "athlete.merge": "Спортсмены объединены (основная запись)",
    "athlete.merged": "Спортсмен объединён с другой записью",
    "horse.merge": "Лошади объединены (основная запись)",
    "horse.merged": "Лошадь объединена с другой записью",
    "settings.save": "Изменены настройки сервиса",
    "export.tournaments": "Выгрузка турниров",
    "export.users": "Выгрузка пользователей",
    "export.tickets": "Выгрузка обращений",
    "user.moderator_on": "Назначен модератором",
    "user.moderator_off": "Снят с роли модератора",
    "service.backup": "Резервная копия сделана",
    "service.error": "Ошибка сервера",
}


class AuditLog(models.Model):
    """Служебная история: кто, что и когда сделал. Пользователи её не редактируют."""

    created_at = models.DateTimeField("время", auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="автор", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField("действие", max_length=64)
    target_type = models.CharField("объект", max_length=64)
    target_id = models.BigIntegerField("id объекта")
    data = models.JSONField("сведения", default=dict, blank=True)

    class Meta:
        verbose_name = "запись истории"
        verbose_name_plural = "история"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_type", "target_id", "-created_at"])]

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.action} {self.target_type}#{self.target_id}"

    @property
    def label(self):
        return ACTION_LABELS.get(self.action, self.action)


def audit(actor, action: str, obj, **data) -> AuditLog:
    if getattr(actor, "is_admin", False):
        data.setdefault("as_admin", True)  # ADM-06: действия администратора отмечаются в журнале
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=obj._meta.model_name,
        target_id=obj.pk,
        data=data,
    )


class Notification(models.Model):
    """Уведомление на сайте. «Требует действия» вычисляется из состояния объекта, а не хранится навсегда."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    text = models.CharField(max_length=300)
    url = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=40, blank=True)
    count = models.PositiveIntegerField(default=1, help_text="Сколько однотипных событий собрано в одно уведомление")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]


def notify(users, text: str, url: str = "", kind: str = ""):
    """Ставит уведомление каждому пользователю (в той же транзакции, что и действие)."""
    seen = set()
    objs = []
    for u in users:
        if u is None or u.pk in seen:
            continue
        seen.add(u.pk)
        objs.append(Notification(user=u, text=text[:300], url=url, kind=kind))
    Notification.objects.bulk_create(objs)


def notify_merged(users, kind: str, url: str, text_for):
    """Сводное уведомление: пока прежнее такое же (тот же вид и ссылка) не прочитано, увеличиваем счётчик
    и переписываем текст, а не создаём новое. text_for(n) возвращает текст для n событий."""
    from django.db.models import F
    from django.utils import timezone

    seen = set()
    for u in users:
        if u is None or u.pk in seen:
            continue
        seen.add(u.pk)
        n = Notification.objects.select_for_update().filter(user=u, kind=kind, url=url, read_at__isnull=True).first()
        if n:
            n.count = F("count") + 1
            n.save(update_fields=["count"])
            n.refresh_from_db(fields=["count"])
            n.text = text_for(n.count)[:300]
            n.created_at = timezone.now()
            n.save(update_fields=["text", "created_at"])
        else:
            Notification.objects.create(user=u, kind=kind, url=url, text=text_for(1)[:300])


class SiteSettings(models.Model):
    """Настройки сервиса, которые администратор меняет без выпуска кода (ADM-08). Одна запись."""

    moderator_emails = models.TextField(
        "email для уведомлений модератору", blank=True,
        help_text="Можно несколько — каждый с новой строки или через запятую. Сюда приходят письма о каждом обращении (SUP-14).",
    )
    support_telegram = models.CharField("Telegram поддержки", max_length=64, blank=True, help_text="Например, @startlist_help")
    footer_phone = models.CharField("телефон в подвале сайта", max_length=32, blank=True)
    footer_email = models.EmailField("email в подвале сайта", blank=True)
    updated_at = models.DateTimeField("изменено", auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "настройки сервиса"
        verbose_name_plural = "настройки сервиса"

    @classmethod
    def get(cls) -> "SiteSettings":
        obj = cls.objects.order_by("pk").first()
        return obj or cls(pk=1)

    @property
    def moderator_email_list(self) -> list[str]:
        import re

        return [a for a in (x.strip() for x in re.split(r"[\s,;]+", self.moderator_emails or "")) if a]

    @property
    def telegram(self) -> str:
        return (self.support_telegram or getattr(settings, "SUPPORT_TELEGRAM", "") or "").strip()


class OutgoingMail(models.Model):
    """Журнал писем (ADM-09). Письмо сначала записывается, затем отправляется; сбой не теряет письмо —
    команда send_mail_queue повторяет отправку (SUP-14)."""

    class State(models.TextChoices):
        PENDING = "pending", "В очереди"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка"

    MAX_ATTEMPTS = 5

    created_at = models.DateTimeField("создано", auto_now_add=True, db_index=True)
    kind = models.CharField("вид", max_length=40, blank=True)
    to = models.TextField("кому")
    subject = models.CharField("тема", max_length=300)
    body = models.TextField("текст")
    state = models.CharField("состояние", max_length=10, choices=State.choices, default=State.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField("попыток", default=0)
    error = models.CharField("ошибка", max_length=300, blank=True)
    sent_at = models.DateTimeField("отправлено", null=True, blank=True)

    class Meta:
        verbose_name = "письмо"
        verbose_name_plural = "журнал писем"
        ordering = ["-created_at", "-id"]

    @property
    def recipients(self) -> list[str]:
        return [a for a in self.to.split(",") if a]
