from django.conf import settings
from django.db import models


ACTION_LABELS = {
    "tournament.create": "Создан черновик",
    "tournament.edit": "Изменены сведения",
    "tournament.submit": "Отправлен на проверку",
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
