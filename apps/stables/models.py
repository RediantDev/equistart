"""Денники и услуги турнира (ТЗ, раздел 11)."""

import datetime

from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class StableCategory(models.Model):
    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.CASCADE, related_name="stable_categories")
    name = models.CharField("название", max_length=120, help_text="Например: «Летний денник», «Денник в конюшне»")
    available_from = models.DateField("первый заезд")
    available_to = models.DateField("последний выезд")
    capacity = models.PositiveIntegerField("вместимость")
    checkin_time = models.TimeField("заезд с", null=True, blank=True)
    checkout_time = models.TimeField("выезд до", null=True, blank=True)
    conditions = models.TextField("условия", blank=True)
    is_active = models.BooleanField("принимать брони", default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name

    def nights(self):
        d, out = self.available_from, []
        while d < self.available_to:
            out.append(d)
            d += datetime.timedelta(days=1)
        return out


class Booking(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Ожидает подтверждения"
        CONFIRMED = "confirmed", "Подтверждена"
        REJECTED = "rejected", "Отклонена"
        CANCELLED = "cancelled", "Отменена"

    HOLDING = (State.PENDING, State.CONFIRMED)

    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.PROTECT, related_name="bookings")
    category = models.ForeignKey(StableCategory, on_delete=models.PROTECT, related_name="bookings")
    horse = models.ForeignKey("profiles.Horse", on_delete=models.PROTECT, related_name="bookings")
    horse_name = models.CharField(max_length=120)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="bookings")
    arrive = models.DateField("заезд")
    depart = models.DateField("выезд")
    required = models.BooleanField("без денника выступления невозможны", default=False)
    contact_phone = models.CharField("телефон для связи по брони", max_length=13, blank=True)
    comment = models.CharField("комментарий", max_length=500, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    reason = models.CharField("причина", max_length=300, blank=True)
    box_number = models.CharField("номер денника", max_length=20, blank=True)
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["arrive", "id"]

    @property
    def nights(self):
        return (self.depart - self.arrive).days

    @property
    def is_holding(self):
        return self.state in self.HOLDING


class Service(models.Model):
    class Recipient(models.TextChoices):
        HORSE = "horse", "Лошадь"
        PERSON = "person", "Представитель"
        TRAILER = "trailer", "Коневоз"

    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.CASCADE, related_name="services")
    name = models.CharField("название", max_length=120, help_text="Опилки, сено, электричество для коневоза…")
    unit = models.CharField("единица", max_length=30, help_text="мешок, тюк, сутки")
    recipient = models.CharField("получатель", max_length=8, choices=Recipient.choices, default=Recipient.HORSE)
    available_from = models.DateField("с", null=True, blank=True)
    available_to = models.DateField("по", null=True, blank=True)
    limit = models.PositiveIntegerField("лимит, единиц", null=True, blank=True)
    is_active = models.BooleanField("принимать заказы", default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.name


class ServiceOrder(models.Model):
    class State(models.TextChoices):
        ORDERED = "ordered", "Заказано"
        CANCELLED = "cancelled", "Отменено"

    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="orders")
    tournament = models.ForeignKey("tournaments.Tournament", on_delete=models.PROTECT, related_name="service_orders")
    ordered_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="service_orders")
    horse = models.ForeignKey("profiles.Horse", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    recipient_label = models.CharField(max_length=120)
    for_date = models.DateField("на дату", null=True, blank=True)
    quantity = models.PositiveIntegerField()
    delivered = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ORDERED)

    @property
    def remaining(self):
        """Сколько осталось выдать — по умолчанию в форме выдачи предлагается остаток, а не весь заказ."""
        return max(0, self.quantity - self.delivered)
    comment = models.CharField(max_length=300, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["for_date", "id"]

    @property
    def delivery_state(self):
        if self.state == self.State.CANCELLED:
            return "Отменено"
        if self.delivered == 0:
            return "Не выдано"
        if self.delivered < self.quantity:
            return f"Выдано частично: {self.delivered} из {self.quantity}"
        return "Выдано"


class Delivery(models.Model):
    order = models.ForeignKey(ServiceOrder, on_delete=models.PROTECT, related_name="deliveries")
    quantity = models.PositiveIntegerField()
    note = models.CharField(max_length=200, blank=True)
    by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "id"]
