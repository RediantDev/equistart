"""Нормализация российских мобильных номеров к виду +79XXXXXXXXX."""

import re

from django.core.exceptions import ValidationError

_ALLOWED = re.compile(r"^[\d\s\-()+.]+$")


def normalize_phone(raw: str) -> str:
    """Возвращает номер в виде +79XXXXXXXXX или бросает ValidationError.

    Принимает: +7 999 123-45-67, 8 (999) 123-45-67, 79991234567, 9991234567.
    Только мобильные номера России (код 9xx), т.к. на номер приходит код подтверждения.
    """
    value = (raw or "").strip()
    if not value:
        raise ValidationError("Укажите номер телефона.", code="required")
    if not _ALLOWED.match(value) or value.count("+") > 1 or ("+" in value and not value.startswith("+")):
        raise ValidationError("Номер может содержать только цифры, пробелы, скобки и дефисы.", code="invalid")

    digits = re.sub(r"\D", "", value)
    if value.startswith("+"):
        if not digits.startswith("7"):
            raise ValidationError("Поддерживаются только российские номера (+7).", code="country")
    elif len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits

    if len(digits) != 11 or not digits.startswith("7"):
        raise ValidationError("Введите номер полностью: +7 и 10 цифр.", code="length")
    if digits[1] != "9":
        raise ValidationError("Укажите мобильный номер (начинается с +7 9…).", code="mobile")
    return "+" + digits


def format_phone(normalized: str) -> str:
    """+79991234567 → +7 999 123-45-67"""
    d = normalized[2:]
    if len(d) != 10:
        return normalized
    return f"+7 {d[0:3]} {d[3:6]}-{d[6:8]}-{d[8:10]}"
