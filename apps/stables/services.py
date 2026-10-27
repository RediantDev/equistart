"""Денники и услуги: вместимость, брони, назначение денников, заказы и выдача (ТЗ, раздел 11)."""

import datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.models import audit, notify
from apps.tournaments.models import TeamMember, Tournament
from apps.tournaments.services import roles_of

from .models import Booking, Delivery, Service, ServiceOrder, StableCategory

STABLE_ROLES = {TeamMember.Role.ORGANIZER, TeamMember.Role.STABLE}


class Conflict(Exception):
    pass


def can_stable(user, tournament) -> bool:
    return bool(roles_of(user, tournament) & STABLE_ROLES)


def require_stable(user, tournament):
    if not can_stable(user, tournament):
        raise PermissionDenied


def _staff(t):
    ids = set(TeamMember.objects.filter(tournament=t, state=TeamMember.State.ACCEPTED, role__in=STABLE_ROLES).values_list("user_id", flat=True))
    ids.add(t.owner_id)
    from django.contrib.auth import get_user_model

    return list(get_user_model().objects.filter(pk__in=ids))


def _days(a, b):
    d = a
    while d < b:
        yield d
        d += datetime.timedelta(days=1)


def occupancy(category, exclude_booking=None) -> dict:
    """Занято денников по ночам: {дата: n}. Держат место ожидающие и подтверждённые брони."""
    occ = {}
    qs = category.bookings.filter(state__in=Booking.HOLDING)
    if exclude_booking:
        qs = qs.exclude(pk=exclude_booking)
    for b in qs:
        for d in _days(b.arrive, b.depart):
            occ[d] = occ.get(d, 0) + 1
    return occ


def free_for(category, arrive, depart) -> int:
    occ = occupancy(category)
    return min((category.capacity - occ.get(d, 0) for d in _days(arrive, depart)), default=category.capacity)


def check_period(category, arrive, depart):
    if not arrive or not depart:
        raise ValueError("Укажите даты заезда и выезда.")
    if depart <= arrive:
        raise ValueError("Выезд должен быть позже заезда (минимум одни сутки).")
    if arrive < category.available_from or depart > category.available_to:
        raise ValueError(f"Категория доступна с {category.available_from:%d.%m} по {category.available_to:%d.%m}.")


def booking_open(t) -> bool:
    return t.publication == Tournament.Publication.PUBLISHED


# ---------------- брони ----------------

@transaction.atomic
def book(user, category_id, horse, arrive, depart, key, required=False, contact_phone="", comment="") -> Booking:
    """LOD-02/03: вся проверка вместимости — в одной транзакции под блокировкой категории."""
    existing = Booking.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.requested_by_id != user.pk:
            raise PermissionDenied
        return existing
    c = StableCategory.objects.select_for_update().select_related("tournament").get(pk=category_id)
    t = c.tournament
    if not booking_open(t) or not c.is_active:
        raise ValueError("Бронирование в этой категории закрыто.")
    if horse.manager_id != user.pk and not can_stable(user, t):
        raise PermissionDenied  # тест 4.2: организатор и конюшня бронируют за участника вручную
    check_period(c, arrive, depart)
    if Booking.objects.filter(tournament=t, horse=horse, state__in=Booking.HOLDING, arrive__lt=depart, depart__gt=arrive).exists():
        raise ValueError(f"У лошади «{horse.name}» уже есть бронь на эти даты.")
    if free_for(c, arrive, depart) < 1:
        raise Conflict("Свободных денников на эти даты нет — последний только что забронировали.")
    b = Booking.objects.create(
        tournament=t, category=c, horse=horse, horse_name=horse.name, requested_by=user, arrive=arrive, depart=depart,
        required=bool(required), contact_phone=contact_phone or "", comment=(comment or "").strip()[:500], idempotency_key=key,
    )
    audit(user, "booking.create", b, category=c.pk, arrive=str(arrive), depart=str(depart), required=b.required)
    from apps.core.models import notify_merged

    notify_merged(_staff(t), "booking", reverse("stables:manage", args=[t.pk]) + "?tab=bookings&state=pending",
                  lambda n: f"«{t.pub_title}»: новых броней денников — {n}. Последняя: {c.name}, {horse.name}, {arrive:%d.%m}–{depart:%d.%m}.")
    return b


def _lock_booking(user, booking_id, revision=None):
    b = Booking.objects.select_for_update().select_related("tournament", "category").get(pk=booking_id)
    require_stable(user, b.tournament)
    if revision is not None and str(revision) != str(b.revision):
        raise Conflict("Бронь уже изменили — обновите страницу.")
    return b


@transaction.atomic
def decide(user, booking_id, accept: bool, reason="", revision=None) -> Booking:
    b = _lock_booking(user, booking_id, revision)
    if b.state != Booking.State.PENDING:
        raise Conflict(f"Бронь уже в состоянии «{b.get_state_display()}».")
    reason = (reason or "").strip()
    if not accept and not reason:
        raise ValueError("Укажите причину отказа — её увидит представитель.")
    b.state = Booking.State.CONFIRMED if accept else Booking.State.REJECTED
    b.reason, b.decided_by, b.decided_at = reason[:300], user, timezone.now()
    b.revision += 1
    b.save()
    audit(user, "booking.confirm" if accept else "booking.reject", b, reason=reason)
    text = "подтверждена" if accept else f"отклонена: {reason}"
    notify([b.requested_by], f"Бронь денника ({b.horse_name}, {b.arrive:%d.%m}–{b.depart:%d.%m}) {text}", reverse("stables:my"), "booking")
    return b


@transaction.atomic
def assign_box(user, booking_id, number) -> Booking:
    b = _lock_booking(user, booking_id)
    if b.state != Booking.State.CONFIRMED:
        raise ValueError("Номер денника назначается после подтверждения брони.")
    number = (number or "").strip().upper()[:20]
    if number:
        # блокируем все брони турнира с этим номером, чтобы два назначения не прошли одновременно
        clash = (Booking.objects.select_for_update().filter(tournament=b.tournament, box_number=number, state=Booking.State.CONFIRMED,
                                                            arrive__lt=b.depart, depart__gt=b.arrive).exclude(pk=b.pk).first())
        if clash:
            raise Conflict(f"Денник {number} занят: {clash.horse_name}, {clash.arrive:%d.%m}–{clash.depart:%d.%m}.")
    b.box_number = number
    b.revision += 1
    b.save(update_fields=["box_number", "revision"])
    audit(user, "booking.box", b, box=number)
    if number:
        notify([b.requested_by], f"{b.horse_name}: назначен денник {number}", reverse("stables:my"), "booking")
    return b


@transaction.atomic
def cancel_booking(user, booking_id, reason="") -> Booking:
    b = Booking.objects.select_for_update().select_related("tournament", "category").get(pk=booking_id)
    staff = can_stable(user, b.tournament)
    if b.requested_by_id != user.pk and not staff:
        raise PermissionDenied
    if b.state not in Booking.HOLDING:
        raise Conflict("Бронь уже закрыта.")
    reason = (reason or "").strip()
    if staff and b.requested_by_id != user.pk and not reason:
        raise ValueError("Укажите причину отмены.")
    b.state, b.reason = Booking.State.CANCELLED, (reason or "Отменено представителем")[:300]
    b.revision += 1
    b.save()
    audit(user, "booking.cancel", b, reason=b.reason)
    if b.requested_by_id != user.pk:
        notify([b.requested_by], f"Бронь денника ({b.horse_name}) отменена: {b.reason}", reverse("stables:my"), "booking")
    else:
        notify(_staff(b.tournament), f"Представитель отменил бронь: {b.horse_name}, {b.arrive:%d.%m}–{b.depart:%d.%m}",
               reverse("stables:manage", args=[b.tournament_id]) + "?tab=bookings", "booking")
    return b


def blocked_horses(tournament_id) -> set:
    """Лошади турнира, у которых отмечено «без денника нельзя», а подтверждённой брони нет — одним запросом."""
    rows = Booking.objects.filter(tournament_id=tournament_id).exclude(state=Booking.State.CANCELLED).values_list("horse_id", "required", "state")
    need, ok = set(), set()
    for horse_id, required, state in rows:
        if required:
            need.add(horse_id)
        if state == Booking.State.CONFIRMED:
            ok.add(horse_id)
    return need - ok


def stable_dependency_ok(entry) -> bool:
    """LOD-05: если по лошади отмечено «без денника нельзя» — нужен подтверждённый денник. Касается только этой лошади."""
    qs = Booking.objects.filter(tournament_id=entry.tournament_id, horse_id=entry.horse_id)
    required = [b for b in qs if b.required and b.state != Booking.State.CANCELLED]
    if not required:
        return True
    return any(b.state == Booking.State.CONFIRMED for b in qs)


@transaction.atomic
def save_category(user, tournament, data: dict, category=None) -> StableCategory:
    require_stable(user, tournament)
    if category is not None:
        category = StableCategory.objects.select_for_update().get(pk=category.pk, tournament=tournament)
        occ = occupancy(category)
        peak = max(occ.values(), default=0)
        if data["capacity"] < peak:
            raise ValueError(f"Нельзя уменьшить вместимость ниже занятой: на одну из ночей уже {peak} броней.")
        outside = category.bookings.filter(state__in=Booking.HOLDING).exclude(arrive__gte=data["available_from"], depart__lte=data["available_to"]).exists()
        if outside:
            raise ValueError("Есть брони за пределами новых дат — сначала отмените или перенесите их.")
        for k, v in data.items():
            setattr(category, k, v)
        category.save()
        audit(user, "stable_category.update", category, **{k: str(v) for k, v in data.items()})
        return category
    c = StableCategory.objects.create(tournament=tournament, **data)
    audit(user, "stable_category.create", c, name=c.name)
    return c


# ---------------- услуги ----------------

def ordered_total(service, exclude=None) -> int:
    qs = service.orders.filter(state=ServiceOrder.State.ORDERED)
    if exclude:
        qs = qs.exclude(pk=exclude)
    return sum(o.quantity for o in qs)


@transaction.atomic
def order(user, service_id, quantity, key, horse=None, trailer="", for_date=None, comment="") -> ServiceOrder:
    existing = ServiceOrder.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.ordered_by_id != user.pk:
            raise PermissionDenied
        return existing
    s = Service.objects.select_for_update().select_related("tournament").get(pk=service_id)
    t = s.tournament
    if not booking_open(t) or not s.is_active:
        raise ValueError("Заказ этой услуги закрыт.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("Количество — целое число.")
    if quantity < 1:
        raise ValueError("Количество — не меньше 1.")
    if s.recipient == Service.Recipient.HORSE:
        if horse is None:
            raise ValueError("Выберите лошадь.")
        if horse.manager_id != user.pk and not can_stable(user, t):
            raise PermissionDenied
        label = horse.name
    elif s.recipient == Service.Recipient.TRAILER:
        trailer = (trailer or "").strip()
        if not trailer:
            raise ValueError("Укажите номер коневоза.")
        label, horse = f"Коневоз {trailer[:40]}", None
    else:
        label, horse = user.full_name, None
    if for_date and ((s.available_from and for_date < s.available_from) or (s.available_to and for_date > s.available_to)):
        raise ValueError("Услуга недоступна на эту дату.")
    if s.limit is not None and ordered_total(s) + quantity > s.limit:
        raise Conflict(f"Осталось только {max(0, s.limit - ordered_total(s))} {s.unit}.")
    o = ServiceOrder.objects.create(service=s, tournament=t, ordered_by=user, horse=horse, recipient_label=label, for_date=for_date, quantity=quantity,
                                    comment=(comment or "").strip()[:300], idempotency_key=key)
    audit(user, "service_order.create", o, service=s.pk, quantity=quantity)
    from apps.core.models import notify_merged

    notify_merged(_staff(t), "service_order", reverse("stables:manage", args=[t.pk]) + "?tab=orders",
                  lambda n: f"«{t.pub_title}»: новых заказов услуг — {n}. Последний: {s.name} × {quantity} ({label}).")
    return o


@transaction.atomic
def cancel_order(user, order_id, reason="") -> ServiceOrder:
    o = ServiceOrder.objects.select_for_update().select_related("tournament", "service").get(pk=order_id)
    staff = can_stable(user, o.tournament)
    if o.ordered_by_id != user.pk and not staff:
        raise PermissionDenied
    if o.state != ServiceOrder.State.ORDERED:
        raise Conflict("Заказ уже отменён.")
    if o.delivered:
        raise ValueError("Часть заказа уже выдана — отменить нельзя.")
    o.state = ServiceOrder.State.CANCELLED
    o.save(update_fields=["state"])
    audit(user, "service_order.cancel", o, reason=reason)
    return o


@transaction.atomic
def deliver(user, order_id, quantity, note="") -> ServiceOrder:
    """SRV-02: отметка фактической выдачи, частичная — допустима, не больше заказанного."""
    o = ServiceOrder.objects.select_for_update().select_related("tournament", "service").get(pk=order_id)
    require_stable(user, o.tournament)
    if o.state != ServiceOrder.State.ORDERED:
        raise ValueError("Заказ отменён.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValueError("Количество — целое число.")
    if quantity < 1:
        raise ValueError("Количество — не меньше 1.")
    if o.delivered + quantity > o.quantity:
        raise ValueError(f"Нельзя выдать больше заказанного: осталось {o.quantity - o.delivered} {o.service.unit}.")
    Delivery.objects.create(order=o, quantity=quantity, note=(note or "").strip()[:200], by=user)
    o.delivered += quantity
    o.save(update_fields=["delivered"])
    audit(user, "service_order.deliver", o, quantity=quantity, total=o.delivered)
    return o


def finish_warnings(tournament) -> list[str]:
    out = []
    pending = Booking.objects.filter(tournament=tournament, state=Booking.State.PENDING).count()
    if pending:
        out.append(f"Брони без решения: {pending}")
    no_box = Booking.objects.filter(tournament=tournament, state=Booking.State.CONFIRMED, box_number="").count()
    if no_box:
        out.append(f"Подтверждённые брони без номера денника: {no_box}")
    undelivered = sum(1 for o in ServiceOrder.objects.filter(tournament=tournament, state=ServiceOrder.State.ORDERED) if o.delivered < o.quantity)
    if undelivered:
        out.append(f"Заказы услуг выданы не полностью: {undelivered}")
    return out
