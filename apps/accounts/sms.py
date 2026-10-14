"""Отправка СМС с кодом подтверждения телефона.

SMS_BACKEND:
  console — код пишется в журнал сервера (разработка и закрытый тест);
  smsru   — шлюз SMS.ru (нужен SMSRU_API_ID; сервера в России).
"""

import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings

log = logging.getLogger("equistart.sms")


class SmsError(Exception):
    pass


def send(phone: str, text: str) -> None:
    backend = settings.SMS_BACKEND
    if backend == "console":
        log.warning("SMS %s: %s", phone, text)
        return
    if backend == "smsru":
        if not settings.SMSRU_API_ID:
            raise SmsError("Не задан SMSRU_API_ID.")
        q = urllib.parse.urlencode({"api_id": settings.SMSRU_API_ID, "to": phone.lstrip("+"), "msg": text, "json": 1})
        try:
            with urllib.request.urlopen(f"https://sms.ru/sms/send?{q}", timeout=10) as r:
                data = json.loads(r.read().decode())
        except Exception as e:  # сеть, таймаут, неверный ответ
            raise SmsError(str(e)) from e
        if data.get("status") != "OK":
            raise SmsError(data.get("status_text") or "шлюз отклонил отправку")
        sms = (data.get("sms") or {}).get(phone.lstrip("+"), {})
        if sms.get("status") not in (None, "OK"):
            raise SmsError(sms.get("status_text") or "шлюз отклонил номер")
        return
    raise SmsError(f"Неизвестный SMS_BACKEND: {backend}")
