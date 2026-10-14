"""Учёт денег: начисления, оплаты, корректировки, возвраты (ТЗ, раздел 12)."""

import datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.models import audit, notify
from apps.tournaments.models import TeamMember
from apps.tournaments.services import roles_of

from .models import Adjustment, Allocation, Charge, Payment, Refund

CASH_ROLES = {TeamMember.Role.ORGANIZER, TeamMember.Role.CASHIER}


def rub(kopecks: int) -> str:
    r, k = divmod(abs(int(kopecks)), 100)
    s = f"{r:,}".replace(",", " ")
    return ("−" if kopecks < 0 else "") + (f"{s},{k:02d} ₽" if k else f"{s} ₽")


def parse_rub(raw) -> int:
    """«1 500», «1500,50», «1500.5» → копейки. Пустое/неверное — ValueError."""
    s = str(raw or "").replace(" ", "").replace(" ", "").replace("₽", "").replace(",", ".").strip()
    if not s:
        raise ValueError("Укажите сумму.")
    try:
        if "." in s:
            r, k = s.split(".", 1)
            if len(k) > 2 or not (r or "0").lstrip("-").isdigit() or not k.isdigit():
                raise ValueError
            sign = -1 if r.startswith("-") else 1
            return sign * (abs(int(r or "0")) * 100 + int(k.ljust(2, "0")))
        return int(s) * 100
    except ValueError:
        raise ValueError(f"«{raw}» — не сумма. Пример: 1500 или 1500,50.")


def can_cash(user, tournament) -> bool:
    return bool(roles_of(user, tournament) & CASH_ROLES)


def require_cash(user, tournament):
    if not can_cash(user, tournament):
        raise PermissionDenied


def _url(t, payer_id):
    return reverse("finance:my") + f"?t={t.pk}"


# ---------------- начисления ----------------

def create_charge(*, tournament, payer, direction, kind, source_key, title, amount, subtitle="", entry=None, by=None) -> Charge:
    """Идемпотентно: повтор с тем же source_key возвращает существующее начисление."""
    c, created = Charge.objects.get_or_create(
        source_key=source_key,
        defaults=dict(tournament=tournament, payer=payer, direction=direction, kind=kind, title=title[:300], subtitle=subtitle[:300],
                      base_kopecks=int(amount), entry=entry, created_by=by),
    )
    if created:
        audit(by, "charge.create", c, amount=int(amount), source=source_key)
    return c


def change_amount(charge_key, new_amount, by, reason):
    """Цена позиции изменилась — корректировка на разницу (FIN-04)."""
    c = Charge.objects.select_for_update().filter(source_key=charge_key).first()
    if not c:
        return None
    delta = int(new_amount) - c.amount
    if delta:
        Adjustment.objects.create(charge=c, delta_kopecks=delta, reason=reason[:300], created_by=by)
        audit(by, "charge.adjust", c, delta=delta, reason=reason)
        notify([c.payer], f"Начисление изменено: {c.title} — {rub(c.amount)}", _url(c.tournament, c.payer_id), "money")
    return c


def source_cancelled(charge_key, by, note, write_off=False, refund_paid=False):
    """Позиция отменена. По умолчанию решение о списании и возврате принимает касса по условиям турнира."""
    c = Charge.objects.select_for_update().filter(source_key=charge_key).first()
    if not c:
        return None
    c.source_cancelled = True
    c.source_note = note[:300]
    c.save(update_fields=["source_cancelled", "source_note"])
    if write_off and c.amount:
        Adjustment.objects.create(charge=c, delta_kopecks=-c.amount, reason=f"{note}: начисление снято", created_by=by)
    if refund_paid and c.refundable > 0:
        Refund.objects.create(charge=c, amount_kopecks=c.refundable, reason=note[:300], created_by=by)
    audit(by, "charge.source_cancelled", c, note=note, write_off=write_off, refund=refund_paid)
    return c


def charge_restored(charge_key, by, note):
    c = Charge.objects.select_for_update().filter(source_key=charge_key).first()
    if c and c.source_cancelled:
        c.source_cancelled = False
        c.source_note = ""
        c.save(update_fields=["source_cancelled", "source_note"])
        audit(by, "charge.source_restored", c, note=note)
    return c


@transaction.atomic
def tournament_cancelled(t, by):
    """FIN-05: отмена турнира — неоплаченное снимается, по оплаченному создаются задачи возврата (не факт)."""
    created = 0
    for c in Charge.objects.select_for_update().filter(tournament=t):
        c.source_cancelled = True
        c.source_note = "Турнир отменён"
        c.save(update_fields=["source_cancelled", "source_note"])
        if c.amount:
            Adjustment.objects.create(charge=c, delta_kopecks=-c.amount, reason="Турнир отменён: начисление снято", created_by=by)
        if c.refundable > 0:
            Refund.objects.create(charge=c, amount_kopecks=c.refundable, reason="Турнир отменён", created_by=by)
            created += 1
    audit(by, "finance.tournament_cancelled", t, refunds=created)
    return created


# ---------------- касса ----------------

@transaction.atomic
def record_payment(user, tournament, payer_id, amount, method, paid_on, charge_ids, key, note="") -> Payment:
    """Отметить оплату. Сумма распределяется по выбранным позициям плательщика по порядку, частичная оплата допустима."""
    require_cash(user, tournament)
    existing = Payment.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.recorded_by_id != user.pk or existing.tournament_id != tournament.pk:
            raise PermissionDenied
        return existing
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Сумма оплаты должна быть больше нуля.")
    if method not in Payment.Method.values:
        raise ValueError("Выберите способ оплаты.")
    if not isinstance(paid_on, datetime.date):
        raise ValueError("Укажите дату оплаты.")
    if paid_on > timezone.localdate() + datetime.timedelta(days=1):
        raise ValueError("Дата оплаты не может быть в будущем.")
    charges = list(Charge.objects.select_for_update().filter(tournament=tournament, payer_id=payer_id, pk__in=charge_ids).order_by("created_at", "id"))
    if not charges:
        raise ValueError("Отметьте позиции, которые оплачены.")
    due = sum(max(0, c.balance) for c in charges)
    if amount > due:
        raise ValueError(f"Сумма больше остатка по выбранным позициям ({rub(due)}). Переплату не принимаем — проверьте сумму.")
    p = Payment.objects.create(tournament=tournament, payer_id=payer_id, amount_kopecks=amount, method=method, paid_on=paid_on,
                               note=(note or "").strip()[:300], idempotency_key=key, recorded_by=user)
    left = amount
    for c in charges:
        part = min(left, max(0, c.balance))
        if part > 0:
            Allocation.objects.create(payment=p, charge=c, amount_kopecks=part)
            left -= part
        if not left:
            break
    audit(user, "payment.record", p, amount=amount, method=method, paid_on=str(paid_on), charges=[c.pk for c in charges])
    notify([p.payer], f"Отмечена оплата {rub(amount)} ({p.get_method_display().lower()}) — «{tournament.pub_title or tournament.title}»", _url(tournament, payer_id), "money")
    return p


@transaction.atomic
def void_payment(user, payment_id, reason) -> Payment:
    p = Payment.objects.select_for_update().select_related("tournament").get(pk=payment_id)
    require_cash(user, p.tournament)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите причину аннулирования.")
    if p.is_void:
        return p
    ids = set(p.allocations.values_list("charge_id", flat=True))
    charges = list(Charge.objects.select_for_update().filter(pk__in=ids))
    for c in charges:
        part = sum(a.amount_kopecks for a in p.allocations.filter(charge=c))
        if c.paid - part < c.refunded + c.refund_pending:
            raise ValueError(f"По позиции «{c.title}» уже есть возврат из этой оплаты — сначала отмените задачу возврата.")
    p.voided_at, p.voided_by, p.void_reason = timezone.now(), user, reason[:300]
    p.save(update_fields=["voided_at", "voided_by", "void_reason"])
    audit(user, "payment.void", p, reason=reason, amount=p.amount_kopecks)
    notify([p.payer], f"Отметка об оплате {rub(p.amount_kopecks)} аннулирована: {reason}", _url(p.tournament, p.payer_id), "money")
    return p


@transaction.atomic
def adjust(user, charge_id, new_amount, reason) -> Charge:
    c = Charge.objects.select_for_update().select_related("tournament").get(pk=charge_id)
    require_cash(user, c.tournament)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите основание корректировки.")
    new_amount = int(new_amount)
    if new_amount < 0:
        raise ValueError("Сумма не может быть отрицательной.")
    delta = new_amount - c.amount
    if not delta:
        raise ValueError("Сумма не изменилась.")
    Adjustment.objects.create(charge=c, delta_kopecks=delta, reason=reason[:300], created_by=user)
    audit(user, "charge.adjust", c, old=new_amount - delta, new=new_amount, reason=reason)
    notify([c.payer], f"Начисление скорректировано: {c.title} — {rub(new_amount)} ({reason})", _url(c.tournament, c.payer_id), "money")
    return c


@transaction.atomic
def create_refund(user, charge_id, amount, reason) -> Refund:
    c = Charge.objects.select_for_update().select_related("tournament").get(pk=charge_id)
    require_cash(user, c.tournament)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите основание возврата.")
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Сумма возврата должна быть больше нуля.")
    if amount > c.refundable:
        raise ValueError(f"Возврат не может превышать оплаченное ({rub(c.refundable)} доступно с учётом задач).")
    r = Refund.objects.create(charge=c, amount_kopecks=amount, reason=reason[:300], created_by=user)
    audit(user, "refund.create", r, charge=c.pk, amount=amount, reason=reason)
    return r


@transaction.atomic
def complete_refund(user, refund_id, method, done_on) -> Refund:
    r = Refund.objects.select_for_update().select_related("charge__tournament").get(pk=refund_id)
    c = Charge.objects.select_for_update().get(pk=r.charge_id)
    require_cash(user, c.tournament)
    if r.state != Refund.State.OPEN:
        if r.state == Refund.State.DONE:
            return r
        raise ValueError("Задача возврата отменена.")
    if method not in Payment.Method.values:
        raise ValueError("Выберите способ выдачи.")
    if not isinstance(done_on, datetime.date) or done_on > timezone.localdate() + datetime.timedelta(days=1):
        raise ValueError("Укажите дату выдачи (не в будущем).")
    if r.amount_kopecks > c.net_paid:
        raise ValueError("Возврат превышает оплаченное — проверьте отметки оплат.")
    r.state, r.method, r.done_on, r.done_by, r.done_at = Refund.State.DONE, method, done_on, user, timezone.now()
    r.save()
    audit(user, "refund.done", r, amount=r.amount_kopecks, method=method, done_on=str(done_on))
    notify([c.payer], f"Возврат выдан: {rub(r.amount_kopecks)} — {c.title}", _url(c.tournament, c.payer_id), "money")
    return r


@transaction.atomic
def cancel_refund(user, refund_id, reason) -> Refund:
    r = Refund.objects.select_for_update().select_related("charge__tournament").get(pk=refund_id)
    require_cash(user, r.charge.tournament)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите причину отмены задачи.")
    if r.state != Refund.State.OPEN:
        raise ValueError("Отменить можно только невыданный возврат.")
    r.state, r.cancel_reason = Refund.State.CANCELLED, reason[:300]
    r.save(update_fields=["state", "cancel_reason"])
    audit(user, "refund.cancel", r, reason=reason)
    return r


# ---------------- сводки ----------------

def totals(charges) -> dict:
    t = {"amount": 0, "paid": 0, "refunded": 0, "refund_pending": 0, "balance": 0, "debt": 0, "overpaid": 0}
    for c in charges:
        t["amount"] += c.amount
        t["paid"] += c.paid
        t["refunded"] += c.refunded
        t["refund_pending"] += c.refund_pending
        b = c.balance
        t["balance"] += b
        if b > 0:
            t["debt"] += b
        elif b < 0:
            t["overpaid"] -= b
    return t


def application_balance(app):
    """Для страницы заявки: начисления по её позициям."""
    charges = list(Charge.objects.filter(entry__application=app).prefetch_related("adjustments", "refunds"))
    if not charges:
        return None
    return {"charges": charges, "totals": totals(charges)}


def finish_warnings(tournament) -> list[str]:
    out = []
    charges = list(Charge.objects.filter(tournament=tournament))
    t = totals(charges)
    if t["debt"]:
        n = sum(1 for c in charges if c.balance > 0)
        out.append(f"Неоплаченные начисления: {n} на {rub(t['debt'])}")
    open_refunds = Refund.objects.filter(charge__tournament=tournament, state=Refund.State.OPEN).count()
    if open_refunds:
        out.append(f"Невыданные возвраты: {open_refunds}")
    pending = sum(1 for c in charges if c.source_cancelled and c.balance > 0)
    if pending:
        out.append(f"Отменённые позиции без решения о списании: {pending}")
    return out
