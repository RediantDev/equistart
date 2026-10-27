"""Нормализация мобильных номеров к формату E.164.

Россия: +79XXXXXXXXX · Казахстан: +77XXXXXXXXX · Беларусь: +375XXXXXXXXX — с проверкой мобильного кода.
Другие страны (решение 25.10.2026): любой номер в международном формате «+код страны и номер», до 15 цифр.
"""

import re

from django.core.exceptions import ValidationError

_ALLOWED = re.compile(r"^[\d\s\-()+.]+$")
# Мобильные коды Беларуси
_BY_MOBILE = {"25", "29", "33", "44"}

# Длина национального номера и группировка цифр для удобного вывода (страны, откуда чаще всего приезжают).
# Номера остальных стран принимаются по общему правилу и выводятся без группировки.
COUNTRIES = {
    "998": (9, (2, 3, 2, 2)),  # Узбекистан
    "996": (9, (3, 3, 3)),  # Кыргызстан
    "992": (9, (2, 3, 2, 2)),  # Таджикистан
    "993": (8, (2, 2, 2, 2)),  # Туркменистан
    "994": (9, (2, 3, 2, 2)),  # Азербайджан
    "995": (9, (3, 2, 2, 2)),  # Грузия
    "374": (8, (2, 3, 3)),  # Армения
    "373": (8, (2, 3, 3)),  # Молдова
    "380": (9, (2, 3, 2, 2)),  # Украина
    "371": (8, (2, 3, 3)),  # Латвия
    "370": (8, (3, 2, 3)),  # Литва
    "972": (9, (2, 3, 4)),  # Израиль
    "971": (9, (2, 3, 4)),  # ОАЭ
    "90": (10, (3, 3, 2, 2)),  # Турция
    "49": (None, None),  # Германия — длина разная
}

# В БД: +7 и +375 — только мобильные; остальные страны — «+», не с 0, 8–15 цифр.
PHONE_DB_REGEX = r"^\+(79\d{9}|77\d{9}|375(25|29|33|44)\d{7}|[124-689]\d{7,14}|3[0-689]\d{6,13}|37[0-46-9]\d{5,12})$"

_INTL_HINT = "Номер другой страны вводите с «+» и кодом страны, например +998 90 123-45-67."


def _country(digits: str):
    for cc in sorted(COUNTRIES, key=len, reverse=True):
        if digits.startswith(cc):
            return cc
    return None


def _international(digits: str) -> str:
    """Номер не России, Казахстана и Беларуси: только цифры после «+»."""
    if digits[0] == "0":
        raise ValidationError("Код страны не может начинаться с 0. " + _INTL_HINT, code="country")
    if not 8 <= len(digits) <= 15:
        raise ValidationError("Введите номер полностью: код страны и номер (всего от 8 до 15 цифр).", code="length")
    cc = _country(digits)
    if cc and COUNTRIES[cc][0] and len(digits) - len(cc) != COUNTRIES[cc][0]:
        raise ValidationError(f"Номер с кодом +{cc}: после кода нужно {COUNTRIES[cc][0]} цифр.", code="length")
    return "+" + digits


def normalize_phone(raw: str) -> str:
    """Возвращает номер в формате E.164 или бросает ValidationError.

    Принимает привычные варианты записи: +7 999 123-45-67, 8 (999) 123-45-67, 9991234567,
    +375 29 123-45-67, 80 29 123-45-67, +7 701 123-45-67, +998 90 123-45-67, 998901234567.
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

    # Другие страны: с «+» и не +7; без «+» — только если номер начинается с кода известной страны и длина совпала
    if has_plus and digits and not digits.startswith("7"):
        return _international(digits)
    if not has_plus and len(digits) > 11:
        cc = _country(digits)
        if cc and COUNTRIES[cc][0] and len(digits) - len(cc) == COUNTRIES[cc][0]:
            return "+" + digits
        raise ValidationError("Слишком много цифр для номера России. " + _INTL_HINT, code="length")

    # Россия и Казахстан (+7)
    if not has_plus:
        if len(digits) == 11 and digits[0] in "78":
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits

    if len(digits) != 11 or not digits.startswith("7"):
        raise ValidationError("Введите номер полностью: код страны и номер.", code="length")
    if digits[1] not in "97":
        raise ValidationError("Укажите мобильный номер. " + _INTL_HINT, code="mobile")
    return "+" + digits


def format_phone(normalized: str) -> str:
    """+79991234567 → +7 999 123-45-67; +375291234567 → +375 29 123-45-67; +998901234567 → +998 90 123-45-67"""
    if normalized.startswith("+375") and len(normalized) == 13:
        d = normalized[4:]
        return f"+375 {d[0:2]} {d[2:5]}-{d[5:7]}-{d[7:9]}"
    d = normalized[2:]
    if normalized.startswith("+7") and len(d) == 10:
        return f"+7 {d[0:3]} {d[3:6]}-{d[6:8]}-{d[8:10]}"
    digits = normalized[1:]
    cc = _country(digits) if normalized.startswith("+") else None
    if cc and COUNTRIES[cc][1] and len(digits) - len(cc) == COUNTRIES[cc][0]:
        rest, parts, i = digits[len(cc):], [], 0
        for n in COUNTRIES[cc][1]:
            parts.append(rest[i:i + n])
            i += n
        return f"+{cc} {parts[0]} " + "-".join(parts[1:])
    return normalized
