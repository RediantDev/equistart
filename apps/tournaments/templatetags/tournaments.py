import datetime

from django import template

register = template.Library()


@register.filter
def rub(kopecks):
    """12345678 → «123 456,78 ₽»; 250000 → «2 500 ₽»; 0 → «Бесплатно»."""
    if kopecks is None or kopecks == "":
        return ""
    kopecks = int(kopecks)
    if kopecks == 0:
        return "Бесплатно"
    rub_part, kop = divmod(kopecks, 100)
    s = f"{rub_part:,}".replace(",", " ")
    if kop:
        s += f",{kop:02d}"
    return f"{s} ₽"


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


@register.filter
def isodate(value, with_year=True):
    """«2026-11-21» → «21 ноября 2026»."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.date.fromisoformat(value)
        except ValueError:
            return value
    out = f"{value.day} {MONTHS_GEN[value.month - 1]}"
    return f"{out} {value.year}" if with_year else out


@register.simple_tag
def daterange(start, end):
    """«20–21 ноября 2026», «30 ноября — 1 декабря 2026», «20 ноября 2026»."""
    if isinstance(start, str):
        start = datetime.date.fromisoformat(start)
    if isinstance(end, str) and end:
        end = datetime.date.fromisoformat(end)
    if not end or end == start:
        return isodate(start)
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GEN[end.month - 1]} {end.year}"
    if start.year == end.year:
        return f"{start.day} {MONTHS_GEN[start.month - 1]} — {end.day} {MONTHS_GEN[end.month - 1]} {end.year}"
    return f"{isodate(start)} — {isodate(end)}"
