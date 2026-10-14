"""Одноразовое подтверждение телефона кодом из СМС (149-ФЗ)."""

import datetime
import hashlib
import hmac
import secrets

from django.conf import settings
from django.utils import timezone

from apps.core.models import audit

from . import sms
from .models import PhoneCode

CODE_TTL_SECONDS = 10 * 60
RESEND_SECONDS = 60
MAX_PER_DAY = 5
MAX_ATTEMPTS = 5


def _hash(code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def seconds_until_resend(user) -> int:
    last = PhoneCode.objects.filter(user=user).order_by("-created_at").first()
    if not last:
        return 0
    left = RESEND_SECONDS - int((timezone.now() - last.created_at).total_seconds())
    return max(0, left)


def send_code(user) -> None:
    if user.phone_verified:
        return
    if seconds_until_resend(user):
        raise ValueError(f"Новый код можно запросить через {seconds_until_resend(user)} с.")
    day_ago = timezone.now() - datetime.timedelta(days=1)
    if PhoneCode.objects.filter(user=user, created_at__gte=day_ago).count() >= MAX_PER_DAY:
        raise ValueError("Слишком много запросов кода за сутки. Попробуйте завтра или напишите администратору.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    PhoneCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    PhoneCode.objects.create(user=user, code_hash=_hash(code))
    try:
        sms.send(user.phone, f"EquiStart: код {code}. Никому его не сообщайте.")
    except sms.SmsError:
        raise ValueError("Не удалось отправить СМС. Попробуйте позже.")
    audit(user, "phone.code_sent", user)


def check_code(user, code: str) -> bool:
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    pc = PhoneCode.objects.filter(user=user, used_at__isnull=True).order_by("-created_at").first()
    if not pc or (timezone.now() - pc.created_at).total_seconds() > CODE_TTL_SECONDS:
        raise ValueError("Код устарел — запросите новый.")
    if pc.attempts >= MAX_ATTEMPTS:
        raise ValueError("Слишком много неверных попыток — запросите новый код.")
    pc.attempts += 1
    pc.save(update_fields=["attempts"])
    if len(code) != 6 or not hmac.compare_digest(pc.code_hash, _hash(code)):
        raise ValueError("Неверный код.")
    pc.used_at = timezone.now()
    pc.save(update_fields=["used_at"])
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at"])
    audit(user, "phone.verified", user)
    return True
