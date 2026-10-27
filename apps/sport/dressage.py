"""Подсчёт выездки (ТЗ 2.2, DRS-06…DRS-08; docs/RULES.md, раздел 2).

Процент судьи = Σ(оценка × коэффициент) / максимум × 100, со штрафами.
Детская езда: (% техники + % качества езды) / 2; КЮР: (% техники + % артистизма) / 2.
Итог пары = среднее процентов судей бригады, 3 знака после запятой (остаток 0,0015 и больше — вверх).
Проверено на технических результатах «Виват, Россия!» 2026 и «Maxima Masters» 2025 (tests/test_dressage.py).
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

THREE = Decimal("0.001")

# Схемы штрафов (DRS-06). Значения по умолчанию нет: схему выбирают при создании езды (Q7).
# errors — по порядку ошибок в схеме: ("pct", x) — минус x процентов у каждого судьи, ("pts", x) — минус x баллов,
# "elim" — исключение. technical — штраф за каждое техническое нарушение (не накапливается в исключение).
PENALTY_SCHEMES = {
    "fei_senior": {"label": "Взрослые (FEI): 1-я ошибка −2 %, 2-я — исключение", "errors": [("pct", "2")], "technical": ("pct", "0.5")},
    "fei_junior": {"label": "Дети, пони, юноши, молодые лошади (FEI): −0,5 %, −1 %, 3-я — исключение",
                   "errors": [("pct", "0.5"), ("pct", "1")], "technical": ("pct", "0.5")},
    "fksr_children": {"label": "Детский бланк ФКСР: −0,5 %, −1 %, 3-я — исключение; прочие ошибки −2 балла",
                      "errors": [("pct", "0.5"), ("pct", "1")], "technical": ("pts", "2")},
    "fksr_points": {"label": "Бланк ФКСР (баллы): −2, −4 балла, 3-я — исключение; прочие ошибки −2 балла",
                    "errors": [("pts", "2"), ("pts", "4")], "technical": ("pts", "2")},
}
PENALTY_CHOICES = [("", "— выберите —")] + [(k, v["label"]) for k, v in PENALTY_SCHEMES.items()]

KIND_PARTS = {
    "standard": ["technique"],
    "children": ["technique", "quality"],
    "freestyle": ["technique", "artistic"],
}
PART_TITLES = {"technique": "Техника", "quality": "Качество езды", "artistic": "Артистизм"}
ROLE_CHOICES = [("all", "Весь лист"), ("technique", "Техника"), ("quality", "Качество езды")]
LETTERS = ["C", "E", "B", "H", "M"]


def q3(x) -> Decimal:
    return Decimal(x).quantize(THREE, rounding=ROUND_HALF_UP)


def validate_parts(kind: str, parts: list) -> None:
    if kind not in KIND_PARTS:
        raise ValueError("Неизвестный тип подсчёта.")
    keys = [p.get("key") for p in parts]
    if keys != KIND_PARTS[kind]:
        need = ", ".join(PART_TITLES[k] for k in KIND_PARTS[kind])
        raise ValueError(f"Для этого типа езды нужны части листа: {need}.")
    for p in parts:
        items = list(p.get("movements", [])) + list(p.get("collective", []))
        if not items:
            raise ValueError(f"В части «{PART_TITLES[p['key']]}» нет ни одной оценки.")
        for it in items:
            coef = it.get("coef", 1)
            if not isinstance(coef, (int, float)) or coef <= 0 or coef > 10:
                raise ValueError("Коэффициент — число от 1 до 10.")
            if not str(it.get("text", "")).strip():
                raise ValueError("У каждой оценки должно быть описание.")
        if str(p.get("step", "0.5")) not in ("0.5", "0.1"):
            raise ValueError("Шаг оценки — 0,5 или 0,1.")


def items_of(part: dict) -> list[dict]:
    return list(part.get("movements", [])) + list(part.get("collective", []))


def part_max(part: dict) -> Decimal:
    return sum((Decimal(str(it.get("coef", 1))) * 10 for it in items_of(part)), Decimal(0))


def check_mark(value, step: str) -> Decimal:
    """Оценка 0–10 с шагом 0,5 или 0,1."""
    try:
        v = Decimal(str(value).replace(",", "."))
    except Exception:
        raise ValueError("Оценка — число от 0 до 10.")
    if v < 0 or v > 10:
        raise ValueError("Оценка — от 0 до 10.")
    unit = Decimal(step)
    if (v / unit) != (v / unit).to_integral_value():
        raise ValueError("Оценка ставится с шагом 0,5." if step == "0.5" else "Оценка ставится с шагом 0,1.")
    return v


def part_points(part: dict, marks: list) -> Decimal:
    """marks — список оценок по всем пунктам части (движения, затем общие оценки)."""
    items = items_of(part)
    if len(marks) != len(items) or any(m is None for m in marks):
        raise ValueError("Лист заполнен не полностью.")
    return sum((Decimal(str(m)) * Decimal(str(it.get("coef", 1))) for m, it in zip(marks, items)), Decimal(0))


def collective_points(part: dict, marks: list) -> Decimal:
    n = len(part.get("movements", []))
    coll = part.get("collective", [])
    return sum((Decimal(str(m)) * Decimal(str(it.get("coef", 1))) for m, it in zip(marks[n:], coll)), Decimal(0))


def penalties(scheme_key: str, errors: int, technical: int) -> tuple[Decimal, Decimal, bool]:
    """(минус баллов, минус процентов, исключение) по схеме и числу ошибок в схеме / технических нарушений."""
    scheme = PENALTY_SCHEMES.get(scheme_key)
    if not scheme:
        if errors or technical:
            raise ValueError("Для езды не выбрана схема штрафов — укажите её в карточке езды.")
        return Decimal(0), Decimal(0), False
    steps = scheme["errors"]
    if errors > len(steps):
        return Decimal(0), Decimal(0), True
    pts, pct = Decimal(0), Decimal(0)
    for kind, value in steps[:errors]:
        if kind == "pts":
            pts += Decimal(value)
        else:
            pct += Decimal(value)
    kind, value = scheme["technical"]
    if kind == "pts":
        pts += Decimal(value) * technical
    else:
        pct += Decimal(value) * technical
    return pts, pct, False


@dataclass
class JudgeScore:
    position: str
    parts: dict = field(default_factory=dict)  # key -> {"points", "max", "pct"}
    points: Decimal = Decimal(0)  # сумма баллов судьи (до штрафов) — колонка «Баллы»
    pct: Decimal | None = None  # процент судьи со штрафами (без округления)
    collective: Decimal = Decimal(0)


def judge_score(test: dict, position: str, role: str, sheet: dict, errors: int, technical: int) -> JudgeScore:
    """sheet — {part_key: [оценки]} по частям, которые судит эта позиция."""
    pts_pen, pct_pen, _ = penalties(test.get("penalty_scheme", ""), errors, technical)
    js = JudgeScore(position)
    pcts = []
    for part in test["parts"]:
        key = part["key"]
        if role not in ("all", key):
            continue
        marks = sheet.get(key)
        if marks is None:
            raise ValueError("Лист заполнен не полностью.")
        p = part_points(part, marks)
        mx = part_max(part)
        js.points += p
        js.collective += collective_points(part, marks)
        # баллы штрафа вычитаются из техники (или единственной части)
        p_net = p - pts_pen if key == "technique" else p
        pct = p_net / mx * 100
        js.parts[key] = {"points": p, "max": mx, "pct": pct}
        pcts.append(pct)
    if not pcts:
        raise ValueError("У позиции нет частей листа.")
    js.pct = sum(pcts, Decimal(0)) / len(pcts) - pct_pen
    return js


@dataclass
class PairResult:
    judges: list
    pct: Decimal | None
    eliminated: bool = False
    total_points: Decimal = Decimal(0)
    collective: Decimal = Decimal(0)
    artistic: Decimal = Decimal(0)


def pair_result(test: dict, judges: list[JudgeScore], errors: int, technical: int, eliminated: bool = False) -> PairResult:
    """Итог пары. Для детской езды: среднее % техники по судьям техники + среднее % качества — пополам."""
    _, _, elim = penalties(test.get("penalty_scheme", ""), errors, technical)
    if eliminated or elim:
        return PairResult(judges, None, True)
    total = sum((j.points for j in judges), Decimal(0))
    coll = sum((j.collective for j in judges), Decimal(0))
    if test["kind"] == "children":
        by_part = {"technique": [], "quality": []}
        for j in judges:
            for k, v in j.parts.items():
                by_part[k].append(v["pct"])
        if not by_part["technique"] or not by_part["quality"]:
            raise ValueError("Для детской езды нужны судьи техники и качества езды.")
        _, pct_pen, _ = penalties(test.get("penalty_scheme", ""), errors, technical)
        avg = lambda xs: sum(xs, Decimal(0)) / len(xs)  # noqa: E731
        # штраф в процентах уже вычтен у каждого судьи — среднее частей его сохраняет
        pct = (avg(by_part["technique"]) + avg(by_part["quality"])) / 2
        return PairResult(judges, pct, False, total, coll)
    pct = sum((j.pct for j in judges), Decimal(0)) / len(judges)
    art = Decimal(0)
    if test["kind"] == "freestyle":
        arts = [j.parts["artistic"]["pct"] for j in judges if "artistic" in j.parts]
        art = sum(arts, Decimal(0)) / len(arts) if arts else Decimal(0)
    return PairResult(judges, pct, False, total, coll, art)


def rank(rows: list[tuple], tie_rule: str) -> dict:
    """rows — [(key, PairResult)]. Возвращает {key: место}; исключённые мест не получают."""
    scored = [(k, r) for k, r in rows if not r.eliminated and r.pct is not None]

    def sort_key(item):
        r = item[1]
        second = Decimal(0)
        if tie_rule == "collective":
            second = r.collective
        elif tie_rule == "artistic":
            second = r.artistic
        return (q3(r.pct), second)

    scored.sort(key=sort_key, reverse=True)
    places, prev, place = {}, None, 0
    for i, item in enumerate(scored, start=1):
        key = sort_key(item) if tie_rule in ("collective", "artistic") else (sort_key(item)[0],)
        if key != prev:
            place = i
            prev = key
        places[item[0]] = place
    return places


def judge_places(rows: list[tuple], position: str) -> dict:
    """Место пары у конкретного судьи (колонка «Место» в протоколе ФКСР)."""
    vals = []
    for k, r in rows:
        if r.eliminated:
            continue
        j = next((j for j in r.judges if j.position == position), None)
        if j and j.pct is not None:
            vals.append((k, q3(j.pct)))
    vals.sort(key=lambda x: x[1], reverse=True)
    out, prev, place = {}, None, 0
    for i, (k, v) in enumerate(vals, start=1):
        if v != prev:
            place, prev = i, v
        out[k] = place
    return out
