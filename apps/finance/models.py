"""Учёт денег без онлайн-оплаты (ТЗ, раздел 12). Все суммы — целые копейки."""

from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class Charge(models.Model):
    """Начисление по позиции: выступление, бронь денника, заказ услуги или прочее."""

    class Direction(models.TextChoices):
        SPORT = "sport", "Спорт"
        STABLE = "stable", "Лошади, денники, услуги"

    class Kind(models.TextChoices):
        ENTRY = "entry", "Стартовый взнос"
        BOOKING = "booking", "Денник"
        SERVICE = "service", "Услуга"
        OTHER = "other", "Прочее"

    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.PROTECT, related_name="charges")
    payer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="charges", verbose_name="плательщик")
    direction = models.CharField(max_length=8, choices=Direction.choices)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    source_key = models.CharField(max_length=40, unique=True, help_text="entry:12, booking:3, order:7")
    entry = models.ForeignKey("entries.Entry", null=True, blank=True, on_delete=models.PROTECT, related_name="charges")
    title = models.CharField(max_length=300)
    subtitle = models.CharField(max_length=300, blank=True)
    base_kopecks = models.BigIntegerField("первоначальная сумма")
    source_cancelled = models.BooleanField("позиция отменена", default=False)
    source_note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.title

    # Суммы считаются из журналов, а не хранятся (ничего не переписывается).
    # Итерация по .all() использует prefetch_related, если он сделан.
    @property
    def adjusted(self):
        return sum(a.delta_kopecks for a in self.adjustments.all())

    @property
    def amount(self):
        return self.base_kopecks + self.adjusted

    @property
    def paid(self):
        return sum(a.amount_kopecks for a in self.allocations.all() if a.payment.voided_at is None)

    @property
    def refunded(self):
        return sum(r.amount_kopecks for r in self.refunds.all() if r.state == Refund.State.DONE)

    @property
    def refund_pending(self):
        return sum(r.amount_kopecks for r in self.refunds.all() if r.state == Refund.State.OPEN)

    @property
    def net_paid(self):
        return self.paid - self.refunded

    @property
    def balance(self):
        """Остаток к оплате (> 0 — долг, < 0 — переплата)."""
        return self.amount - self.net_paid

    @property
    def refundable(self):
        return max(0, self.net_paid - self.refund_pending)


class Adjustment(models.Model):
    """Корректировка начисления: изменение цены, списание, скидка. Исходная сумма не переписывается."""

    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="adjustments")
    delta_kopecks = models.BigIntegerField()
    reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Payment(models.Model):
    """Факт оплаты, отмеченный кассой. Деньги через платформу не проходят."""

    class Method(models.TextChoices):
        CASH = "cash", "Наличные"
        TRANSFER = "transfer", "Перевод организатору"
        OTHER = "other", "Иное"

    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.PROTECT, related_name="payments")
    payer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="payments")
    amount_kopecks = models.BigIntegerField()
    method = models.CharField(max_length=10, choices=Method.choices)
    paid_on = models.DateField("дата оплаты")
    note = models.CharField(max_length=300, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    void_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def is_void(self):
        return self.voided_at is not None


class Allocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="allocations")
    amount_kopecks = models.BigIntegerField()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount_kopecks__gt=0), name="allocation_positive")]


class Refund(models.Model):
    """Возврат: задача (основание, позиция, сумма) → факт выдачи с автором и датой."""

    class State(models.TextChoices):
        OPEN = "open", "К возврату"
        DONE = "done", "Выдан"
        CANCELLED = "cancelled", "Задача отменена"

    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="refunds")
    amount_kopecks = models.BigIntegerField()
    reason = models.CharField("основание", max_length=300)
    state = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    method = models.CharField(max_length=10, choices=Payment.Method.choices, blank=True)
    done_on = models.DateField(null=True, blank=True)
    done_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    done_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount_kopecks__gt=0), name="refund_positive")]
