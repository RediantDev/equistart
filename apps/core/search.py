"""Поиск, который понимает, как люди пишут (тест 5, п. 9.4–9.5).

«белая роща», «Белая Роща», «кск белая роща», «КСК «Белая роща»» и «березки» вместо «Берёзки»
находят одно и то же: регистр, кавычки, буква «ё» и слова вроде «КСК», «клуб» не мешают поиску.
Каждое слово запроса должно встретиться хотя бы в одном из полей.
"""
import re

from django.db.models import Q, Value
from django.db.models.functions import Lower, Replace

# Слова-«формы» организаций: их можно писать или не писать
STOP_WORDS = {
    "кск", "кк", "ксц", "кфх", "ксб", "ксшор", "сшор", "дюсш", "ооо", "ип", "ано", "оао", "зао", "пао", "мбу", "гбу",
    "конный", "конная", "конное", "конноспортивный", "конно", "спортивный", "спортивная", "спортивное",
    "клуб", "комплекс", "центр", "база", "школа", "клуба", "комплекса", "центра", "им", "имени",
    "г", "город", "д", "дер", "деревня", "пос", "поселок", "посёлок", "с", "село", "обл", "область", "в", "на", "и",
}
_QUOTES = re.compile(r"[«»\"'„“”‚‘’`]")
_SPLIT = re.compile(r"[\s,.;:()/\\\-–—№#]+")


def normalize(text: str) -> str:
    return _QUOTES.sub(" ", (text or "").lower().replace("ё", "е"))


def terms(q: str) -> list[str]:
    words = [w for w in _SPLIT.split(normalize(q)) if w]
    meaningful = [w for w in words if w not in STOP_WORDS]
    # Если запрос состоит только из «КСК» и т. п. — ищем как есть
    return meaningful or words


def _norm_expr(field):
    return Replace(Lower(field), Value("ё"), Value("е"))


def apply(qs, q: str, fields: list[str], extra=None):
    """Отфильтровать queryset: каждое слово запроса — в каком-нибудь из полей.

    extra(term) -> Q | None — дополнительные условия для слова (например, регион по названию).
    """
    words = terms(q)
    if not words:
        return qs
    names = {}
    for i, f in enumerate(fields):
        names[f] = f"_s{i}"
    qs = qs.annotate(**{alias: _norm_expr(f) for f, alias in names.items()})
    for w in words:
        cond = Q()
        for alias in names.values():
            cond |= Q(**{f"{alias}__contains": w})
        if extra:
            more = extra(w)
            if more is not None:
                cond |= more
        qs = qs.filter(cond)
    return qs.distinct() if any("__" in f for f in fields) else qs


def matches(text: str, q: str) -> bool:
    """То же правило для списков в памяти."""
    hay = normalize(text)
    return all(w in hay for w in terms(q))
