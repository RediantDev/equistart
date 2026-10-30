"""Отправка писем через журнал OutgoingMail (ADM-09, SUP-14).

Письмо записывается в той же транзакции, что и действие, и отправляется после её фиксации.
Если почтовый сервер недоступен, письмо остаётся в журнале с ошибкой; команда send_mail_queue
(cron, раз в несколько минут) повторяет отправку до OutgoingMail.MAX_ATTEMPTS раз.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import OutgoingMail

log = logging.getLogger(__name__)


def queue_mail(to, subject: str, body: str, kind: str = "") -> OutgoingMail | None:
    to = to if isinstance(to, (list, tuple, set)) else [to]
    seen, clean = set(), []
    for a in to:
        a = (a or "").strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            clean.append(a)
    if not clean:
        return None
    m = OutgoingMail.objects.create(to=",".join(clean), subject=subject[:300], body=body, kind=kind)
    transaction.on_commit(lambda: deliver(m.pk))
    return m


def deliver(pk: int) -> bool:
    with transaction.atomic():
        m = OutgoingMail.objects.select_for_update(skip_locked=True).filter(pk=pk).first()
        if m is None or m.state == OutgoingMail.State.SENT or m.attempts >= OutgoingMail.MAX_ATTEMPTS:
            return False
        m.attempts += 1
        try:
            send_mail(m.subject, m.body, settings.DEFAULT_FROM_EMAIL, m.recipients, fail_silently=False)
        except Exception as e:  # письмо не должно ломать действие пользователя
            log.exception("mail %s failed", m.pk)
            m.state, m.error = OutgoingMail.State.FAILED, (type(e).__name__ + ": " + str(e))[:300]
            m.save(update_fields=["state", "error", "attempts"])
            return False
        m.state, m.error, m.sent_at = OutgoingMail.State.SENT, "", timezone.now()
        m.save(update_fields=["state", "error", "attempts", "sent_at"])
        return True


def retry_pending() -> tuple[int, int]:
    ids = list(OutgoingMail.objects.exclude(state=OutgoingMail.State.SENT)
               .filter(attempts__lt=OutgoingMail.MAX_ATTEMPTS).order_by("created_at").values_list("pk", flat=True)[:200])
    ok = sum(1 for pk in ids if deliver(pk))
    return ok, len(ids) - ok
