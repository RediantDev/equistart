"""Дисциплины, виды программ и шаблоны полей результата.

Состав — по видам конного спорта ФКСР; таблица на подтверждение заказчику — docs/DISCIPLINES.md.
Вид программы подставляет поля результата по умолчанию; судья может их изменить.
Автоматический подсчёт результатов не выполняется до утверждения матрицы дисциплин.
"""

DISCIPLINES = [
    # (ключ, название, цвет метки)
    ("jumping", "Конкур", "sky"),
    ("dressage", "Выездка", "mint"),
    ("eventing", "Троеборье", "lilac"),
    ("driving", "Драйвинг", "peach"),
    ("endurance", "Дистанционные конные пробеги", "peach"),
    ("vaulting", "Вольтижировка", "pink"),
    ("reining", "Рейнинг", "lemon"),
    ("paradressage", "Пара-выездка", "mint"),
    ("other", "Другая дисциплина", ""),
]
DISCIPLINE_CHOICES = [(k, n) for k, n, _ in DISCIPLINES]
# С чем сервис работает сейчас (тест 5, п. 1.4): остальные скрыты из фильтров календаря
ACTIVE_DISCIPLINES = ("jumping", "dressage")
DISCIPLINE_NAMES = dict(DISCIPLINE_CHOICES)
DISCIPLINE_COLORS = {k: c for k, _, c in DISCIPLINES}

RESULT_FIELDS = {
    "penalties": "Штрафные очки",
    "time": "Время, с",
    "jumpoff_penalties": "Ш/о перепрыжки",
    "jumpoff_time": "Время перепрыжки, с",
    "percent": "Процент",
    "points": "Баллы",
}

# (ключ, название, дисциплина, поля результата по умолчанию)
PROGRAM_TYPES = [
    ("jumping_clear", "На чистоту", "jumping", ["penalties"]),
    ("jumping_clear_time", "На чистоту и резвость", "jumping", ["penalties", "time"]),
    ("jumping_jumpoff", "С перепрыжкой", "jumping", ["penalties", "time", "jumpoff_penalties", "jumpoff_time"]),
    ("jumping_two_phases", "В две фазы", "jumping", ["penalties", "time"]),
    ("jumping_two_rounds", "В два гита", "jumping", ["penalties", "time"]),
    ("jumping_accumulator", "По возрастающей сложности", "jumping", ["points", "time"]),
    ("jumping_speed", "На резвость (таблица С)", "jumping", ["time"]),
    ("jumping_joker", "С джокером", "jumping", ["points", "time"]),
    ("jumping_power", "На мощность", "jumping", ["penalties"]),
    ("jumping_style", "На стиль", "jumping", ["points"]),
    ("jumping_other", "Другой маршрут", "jumping", ["penalties", "time"]),
    ("dressage_test", "Езда (тест)", "dressage", ["percent"]),
    ("dressage_freestyle", "Произвольная программа (КЮР)", "dressage", ["percent"]),
    ("dressage_other", "Другая езда", "dressage", ["percent"]),
    ("eventing_dressage", "Троеборье: манежная езда", "eventing", ["penalties"]),
    ("eventing_cross", "Троеборье: полевые испытания (кросс)", "eventing", ["penalties", "time"]),
    ("eventing_jumping", "Троеборье: конкур", "eventing", ["penalties", "time"]),
    ("eventing_total", "Троеборье: итог", "eventing", ["penalties"]),
    ("driving_dressage", "Драйвинг: манежная езда", "driving", ["penalties"]),
    ("driving_marathon", "Драйвинг: марафон", "driving", ["penalties", "time"]),
    ("driving_cones", "Драйвинг: фигурная езда", "driving", ["penalties", "time"]),
    ("endurance_ride", "Пробег на дистанцию", "endurance", ["time"]),
    ("vaulting_program", "Программа вольтижировки", "vaulting", ["points"]),
    ("reining_pattern", "Паттерн", "reining", ["points"]),
    ("paradressage_test", "Езда (тест) пара-выездки", "paradressage", ["percent"]),
    ("custom", "Своя программа", "other", ["points"]),
]
PROGRAM_TYPE_NAMES = {k: n for k, n, _, _ in PROGRAM_TYPES}
PROGRAM_TYPE_DISCIPLINE = {k: d for k, _, d, _ in PROGRAM_TYPES}
PROGRAM_TYPE_FIELDS = {k: f for k, _, _, f in PROGRAM_TYPES}


def program_type_choices():
    """Виды программ, сгруппированные по дисциплинам (для <optgroup>)."""
    groups = []
    for key, name, _ in DISCIPLINES:
        items = [(k, n) for k, n, d, _ in PROGRAM_TYPES if d == key]
        if items:
            groups.append((name, items))
    return groups


def types_for(discipline: str) -> set[str]:
    allowed = {k for k, _, d, _ in PROGRAM_TYPES if d == discipline}
    allowed.add("custom")
    return allowed
