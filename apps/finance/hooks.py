"""Связь с заявками (вызывается из apps.entries.services.money_hook)."""

from apps.entries.models import Entry
from apps.finance.models import Charge

from . import services


def _payer(e: Entry):
    # Позднюю заявку вносит судья — платит представитель спортсмена (управляющий).
    return e.athlete.manager if e.application.late else e.application.submitted_by


def entry_created(e: Entry, user):
    a, h = e.athlete_snapshot.get("name", ""), e.horse_snapshot.get("name", "")
    services.create_charge(
        tournament=e.tournament, payer=_payer(e), direction=Charge.Direction.SPORT, kind=Charge.Kind.ENTRY,
        source_key=f"entry:{e.pk}", title=f"{e.program_name} · {e.class_name}", subtitle=f"{a} / {h}",
        amount=e.fee_kopecks, entry=e, by=user,
    )


def entry_cancelled(e: Entry, user, reason):
    # Отказ судьи — позиция не состоится по решению турнира: начисление снимается, оплаченное — в задачи возврата.
    # Отмена — решение о списании и возврате принимает касса по условиям отмены турнира (docs/QUESTIONS.md).
    rejected = e.decision == Entry.Decision.REJECTED
    services.source_cancelled(f"entry:{e.pk}", user, reason, write_off=rejected, refund_paid=rejected)


def entry_fee_changed(e: Entry, user, old_fee):
    services.change_amount(f"entry:{e.pk}", e.fee_kopecks, user, f"Изменён зачёт: {e.class_name}")


def entry_restored(e: Entry, user):
    """Отклонённое выступление снова принято — начисление возвращается к цене зачёта."""
    services.charge_restored(f"entry:{e.pk}", user, "Принято после отказа")
    services.change_amount(f"entry:{e.pk}", e.fee_kopecks, user, "Принято после отказа")
