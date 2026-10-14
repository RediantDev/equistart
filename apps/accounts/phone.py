"""Нормализация мобильных номеров России, Казахстана и Беларуси к формату E.164.

Россия: +79XXXXXXXXX · Казахстан: +77XXXXXXXXX · Беларусь: +375XXXXXXXXX
"""

import re

from django.core.exceptions import ValidationError

_ALLOWED = re.compile(r"^[\d\s\-()+.]+$")
# Мобильные коды Беларуси
_BY_MOBILE = {"25", "29", "33", "44"}

PHONE_DB_REGEX = r"^\+(79\d{9}|77\d{9}|375(25|29|33|44)\d{7})$"


def normalize_phone(raw: str) -> str:
    """Возвращает номер в формате E.164 или бросает ValidationError.

    Принимает привычные варианты записи: +7 999 123-45-67, 8 (999) 123-45-67, 9991234567,
    +375 29 123-45-67, 80 29 123-45-67, +7 701 123-45-67.
    Только мобильные номера, т.к. на номер приходит код подтверждения.
    """
    value = (raw or "").strip()
    if not value:
        raise ValidationError("Укажите номер телефона.", code="required")
    if not _ALLOWED.match(value) or value.count("+") > 1 or ("+" in value and not value.startswith("+")):
        raise ValidationError("Номер может содержать только цифры, пробелы, скобки и дефисы.", code="invalid")

    digits = re.sub(r"\D", "", value)
    has_plus = value.startswith("+")

    # Беларусь
    if digits.startswith("375") and (has_plus or len(digits) == 12):
        if len(digits) != 12 or digits[3:5] not in _BY_MOBILE:
            raise ValidationError("Укажите мобильный номер Беларуси: +375 и 9 цифр.", code="by")
        return "+" + digits
    if not has_plus and len(digits) == 11 and digits.startswith("80") and digits[2:4] in _BY_MOBILE:
        return "+375" + digits[2:]

    # Россия и Казахстан (+7)
    if has_plus:
        if not digits.startswith("7"):
            raise ValidationError(
                "Поддерживаются номера России и Казахстана (+7) и Беларуси (+375).", code="country"
            )
    elif len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits

    if len(digits) != 11 or not digits.startswith("7"):
        raise ValidationError("Введите номер полностью: код страны и номер.", code="length")
    if digits[1] not in "97":
        raise ValidationError("Укажите мобильный номер.", code="mobile")
    return "+" + digits


def format_phone(normalized: str) -> str:
    """+79991234567 → +7 999 123-45-67; +375291234567 → +375 29 123-45-67"""
    if normalized.startswith("+375") and len(normalized) == 13:
        d = normalized[4:]
        return f"+375 {d[0:2]} {d[2:5]}-{d[5:7]}-{d[7:9]}"
    d = normalized[2:]
    if normalized.startswith("+7") and len(d) == 10:
        return f"+7 {d[0:3]} {d[3:6]}-{d[6:8]}-{d[8:10]}"
    return normalized
