from django.conf import settings
from django.db import models


ACTION_LABELS = {
    "tournament.create": "Создан черновик",
    "tournament.edit": "Изменены сведения",
    "tournament.submit": "Отправлен на проверку",
    "tournament.approve": "Опубликован модератором",
    "tournament.return": "Возвращён с замечаниями",
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
