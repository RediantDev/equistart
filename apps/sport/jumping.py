"""Конкур: справочник маршрутов (DIR-01) и подсчёт (JMP-02…JMP-05).

Нормы — Правила вида спорта «Конный спорт» (приказ Минспорта №1256 от 29.12.2025), глава X; сводка — docs/RULES.md.
Таблицы штрафов — п. 16; перепрыжки — п. 19; типы маршрутов — пп. 24–42.
Тип маршрута связан с формулой подсчёта в этом файле, поэтому новые типы добавляются вместе с кодом.
"""

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

TABLES = {
    "A": "Таблица A",
    "B1": "Таблица B1",
    "B2": "Таблица B2",
    "C": "Таблица C",
    "STYLE": "Стиль (оценки судей)",
}

# ---------------- справочник маршрутов ----------------


@dataclass(frozen=True)
class RoundSpec:
    key: str  # main, jo1, jo2, phase1, phase2, round1, round2
    title: str
    timed: bool  # учитывается ли время для мест
    jumpoff: bool = False  # перепрыжка: штраф за время 1 очко в секунду по всем таблицам (п. 16)


@dataclass(frozen=True)
class RouteType:
    key: str
    name: str
    article: str  # пункт гл. X Правил
    fei: str = ""  # старый номер FEI, если соответствие надёжно (для положений)
    tables: tuple = ("A", "B1", "B2")
    default_table: str = "A"
    rounds: tuple = ()
    variants: tuple = ()  # (код, название)
    by_obstacle: bool = False  # ввод по каждому препятствию (баллы, джокер)
    manual: bool = False
    note: str = ""


MAIN = RoundSpec("main", "Основной гит", False)
MAIN_T = RoundSpec("main", "Основной гит", True)
JO1_T = RoundSpec("jo1", "Перепрыжка", True, True)
JO1_U = RoundSpec("jo1", "Перепрыжка", False, True)
JO2_T = RoundSpec("jo2", "Вторая перепрыжка", True, True)

ROUTE_TYPES = [
    RouteType("no_time", "Без учёта времени", "16.4.1.1", "238.1.1", rounds=(MAIN,),
              note="Места по штрафным очкам; равные очки делят место."),
    RouteType("no_time_jo", "Без учёта времени, перепрыжка на время", "16.4.1.2", rounds=(MAIN, JO1_T),
              note="Равенство за 1-е место решает одна перепрыжка с учётом времени; остальные места — по очкам основного гита."),
    RouteType("no_time_2jo", "Без учёта времени, две перепрыжки", "16.4.1.3", rounds=(MAIN, JO1_U, JO2_T),
              note="Первая перепрыжка без учёта времени, вторая — с учётом времени."),
    RouteType("clear_speed", "На чистоту и резвость", "16.4.2.1", "238.2.1", rounds=(MAIN_T,),
              note="Места по штрафным очкам, затем по времени."),
    RouteType("jumpoff", "С перепрыжкой", "16.4.2.2", "238.2.2", rounds=(MAIN, JO1_T),
              note="Равенство очков за 1-е место — перепрыжка на время; остальные места — по очкам и времени основного гита."),
    RouteType("jumpoff_2", "С двумя перепрыжками", "16.4.2.3", rounds=(MAIN, JO1_T, JO2_T),
              note="Как «С перепрыжкой»; при новом равенстве — вторая перепрыжка на время."),
    RouteType("jumpoff_immediate", "С перепрыжкой сразу", "16.4.2.2, 19.1.3", rounds=(MAIN, JO1_T),
              note="Пара без штрафов в основном гите сразу проходит перепрыжку. Нельзя в Гран-При."),
    RouteType("two_phases", "В две фазы", "35.1", tables=("A", "B1", "B2", "C"),
              rounds=(RoundSpec("phase1", "1-я фаза", False), RoundSpec("phase2", "2-я фаза", True)),
              variants=(("35.1.5.1", "обе фазы без учёта времени"), ("35.1.5.2", "2-я фаза на время"),
                        ("35.1.5.3", "обе фазы на время"), ("35.1.5.4", "1-я без времени, 2-я по таблице C"),
                        ("35.1.5.5", "1-я на время, 2-я по таблице C")),
              note="Во 2-ю фазу проходят только пары без штрафов в 1-й фазе."),
    RouteType("two_phases_special", "В две фазы, специальный", "35.2",
              rounds=(RoundSpec("phase1", "1-я фаза", False), RoundSpec("phase2", "2-я фаза", True)),
              note="Все закончившие 1-ю фазу продолжают; результат — сумма штрафов фаз, затем время 2-й фазы."),
    RouteType("two_rounds", "В два гита", "34",
              rounds=(RoundSpec("round1", "1-й гит", False), RoundSpec("round2", "2-й гит", True), RoundSpec("jo1", "Перепрыжка", True, True)),
              variants=(("34.3.1", "1-й на время, 2-й без времени, перепрыжка"), ("34.3.2", "оба без времени, перепрыжка"),
                        ("34.3.3.1", "оба на время, без перепрыжки"), ("34.3.3.2", "1-й без времени, 2-й на время, без перепрыжки"),
                        ("34.3.4.1", "оба на время, перепрыжка"), ("34.3.4.2", "1-й без времени, 2-й на время, перепрыжка")),
              note="Во 2-й гит — все закончившие 1-й (34.2.1). Места — по сумме штрафов двух гитов; перепрыжка — при равенстве за 1-е место."),
    RouteType("choose_route", "Выбери свой маршрут", "32", tables=("C",), default_table="C", rounds=(MAIN_T,),
              note="Каждое препятствие один раз в любом порядке, таблица C, лимит 120 с; не прошедшие все препятствия исключаются."),
    RouteType("speed_handiness", "На резвость и управляемость (охотничий)", "26", tables=("C",), default_table="C", rounds=(MAIN_T,),
              note="Таблица C: время + штрафные секунды."),
    RouteType("manual", "Прочий маршрут (ручной ввод)", "", tables=(), default_table="", manual=True,
              note="По возрастающей сложности, «до первой ошибки», «сбил — гони», стилевые, эстафеты, «на выбывание», «сила и ловкость», дерби, решающие гиты: очки, время и место вводит судья. Автоподсчёт этих маршрутов — после согласования спорных норм с судьями."),
]
ROUTE_BY_KEY = {r.key: r for r in ROUTE_TYPES}
ROUTE_CHOICES = [(r.key, r.name) for r in ROUTE_TYPES]


def route(key: str) -> RouteType:
    return ROUTE_BY_KEY.get(key) or ROUTE_BY_KEY["manual"]


def article_label(rt: RouteType, table: str) -> str:
    """«гл. X, п. 16.4.2.2, табл. «A»» — для протокола и программы."""
    if rt.manual:
        return ""
    parts = [f"гл. X, п. {rt.article}"]
    if table and table != "STYLE":
        parts.append(f"табл. «{table}»")
    text = ", ".join(parts)
    if rt.fei:
        text += f" (ст. {rt.fei} FEI)"
    return text


def table_for(rt: RouteType, variant: str, table: str, round_key: str) -> str:
    """Таблица конкретного гита: в 35.1.5.4/35.1.5.5 вторая фаза судится по таблице C."""
    if rt.key == "two_phases" and round_key == "phase2" and variant in ("35.1.5.4", "35.1.5.5"):
        return "C"
    if rt.key == "choose_route":
        return "C"
    return table


def round_timed(rt: RouteType, variant: str, round_key: str) -> bool:
    """Учитывается ли время гита для мест (с учётом варианта формулы)."""
    if rt.key == "two_phases":
        return round_key == "phase2" and variant != "35.1.5.1" or (round_key == "phase1" and variant in ("35.1.5.3", "35.1.5.5"))
    if rt.key == "two_rounds":
        if round_key == "jo1":
            return True
        r1 = variant in ("34.3.1", "34.3.3.1", "34.3.4.1")
        r2 = variant in ("34.3.3.1", "34.3.3.2", "34.3.4.1", "34.3.4.2")
        return r1 if round_key == "round1" else r2
    return route_round(rt, round_key).timed


def rounds_of(rt: RouteType, variant: str) -> list:
    """Гиты маршрута с учётом варианта (у «В два гита» перепрыжки может не быть)."""
    if rt.key == "two_rounds" and variant in ("34.3.3.1", "34.3.3.2"):
        return [r for r in rt.rounds if r.key != "jo1"]
    return list(rt.rounds)


def table_allowed(table: str, max_height: int | None, official: bool) -> str | None:
    """п. 16.2.3.3: таблица B2 не применяется в официальных соревнованиях при высоте 130 см и выше."""
    if table == "B2" and official and max_height and max_height >= 130:
        return "Таблица B2 не применяется в официальных соревнованиях при высоте препятствий 130 см и выше (п. 16.2.3.3)."
    return None


# ---------------- норма времени ----------------

def suggest_time_allowed(length_m: int | None, speed: int | None) -> int | None:
    """Норма времени = длина / скорость (п. 15.2.1). Округление вверх до целой секунды — предложение, судья подтверждает (Q10)."""
    if not length_m or not speed:
        return None
    return math.ceil(Decimal(length_m) * 60 / Decimal(speed))


def time_limit(table: str, time_allowed, length_m: int | None) -> Decimal | None:
    """Лимит: 2 × норма; по таблице C — 120 с (до 600 м) или 180 с (от 600 м) (пп. 15.3.1, 16.5.3)."""
    if table == "C":
        return Decimal(180) if (length_m or 0) >= 600 else Decimal(120)
    if time_allowed:
        return Decimal(time_allowed) * 2
    return None


# ---------------- гит ----------------

@dataclass
class RoundInput:
    """Что вводит судья у поля по одному гиту / фазе / перепрыжке."""

    knockdowns: int = 0
    refusals: int = 0
    corrections: int = 0  # «неповиновение с разрушением»: +6 с к времени
    time: Decimal | None = None
    fall: bool = False
    status: str = "ok"  # ok | retired (сошёл) | eliminated (исключён судьёй) | withdrawn | no_show
    reason: str = ""


@dataclass
class RoundResult:
    penalties: Decimal = Decimal(0)  # очки (A/B1/B2) или итоговые секунды (C)
    obstacle_pen: Decimal = Decimal(0)
    time_pen: Decimal = Decimal(0)
    time: Decimal | None = None  # время с коррекцией
    status: str = "ok"  # ok | eliminated | retired | withdrawn | no_show
    reason: str = ""

    @property
    def finished(self):
        return self.status == "ok"


def _d(x) -> Decimal | None:
    if x is None or x == "":
        return None
    return Decimal(str(x).replace(",", "."))


def score_round(table: str, spec: RoundSpec, inp: RoundInput, time_allowed=None, limit=None, indoor=False) -> RoundResult:
    """Результат одного гита по таблице (п. 16)."""
    r = RoundResult()
    if inp.status in ("withdrawn", "no_show", "retired"):
        r.status = inp.status
        r.reason = inp.reason or {"withdrawn": "Снят", "no_show": "Не стартовал", "retired": "Сошёл"}[inp.status]
        return r
    if inp.status == "eliminated":
        r.status, r.reason = "eliminated", inp.reason or "Исключён"
        return r
    if inp.fall:
        r.status, r.reason = "eliminated", "Падение"
        return r
    t = _d(inp.time)
    if t is not None:
        t = t + 6 * inp.corrections
    r.time = t
    if limit is not None and t is not None and t > Decimal(limit):
        r.status, r.reason = "eliminated", "Превышение лимита времени"
        return r
    if table == "C":
        if inp.refusals >= 2:
            r.status, r.reason = "eliminated", "Второе неповиновение"
            return r
        per = Decimal(3) if (indoor or spec.key == "phase2" or spec.jumpoff) else Decimal(4)
        if t is None:
            raise ValueError("Укажите время.")
        r.obstacle_pen = per * inp.knockdowns
        r.penalties = t + r.obstacle_pen
        return r
    if table == "B2":
        if inp.refusals >= 3:
            r.status, r.reason = "eliminated", "Третье неповиновение"
            return r
        refusal_pen = {0: 0, 1: 3, 2: 6}[inp.refusals]
    else:  # A, B1
        if inp.refusals >= 2:
            r.status, r.reason = "eliminated", "Второе неповиновение"
            return r
        refusal_pen = 4 * inp.refusals
    r.obstacle_pen = Decimal(4 * inp.knockdowns + refusal_pen)
    if time_allowed and t is not None and t > Decimal(time_allowed):
        over = t - Decimal(time_allowed)
        if spec.jumpoff and spec.timed:
            r.time_pen = Decimal(math.ceil(over))
        elif table == "B1":
            r.time_pen = Decimal(math.ceil(over / 4))
        elif table == "B2":
            r.time_pen = Decimal(math.ceil(over)) * Decimal("0.25")
        else:
            r.time_pen = Decimal(math.ceil(over))
    r.penalties = r.obstacle_pen + r.time_pen
    if spec.timed and t is None:
        raise ValueError("Укажите время — оно нужно для мест.")
    return r


# ---------------- места ----------------

@dataclass
class Standing:
    key: object
    rounds: dict = field(default_factory=dict)  # spec.key -> RoundResult
    place: int | None = None
    status: str = "ok"
    reason: str = ""
    sort: tuple = ()
    total: Decimal | None = None  # итог для показа (сумма штрафов двух фаз и т.п.)


def _assign(groups: list[list[Standing]], start: int = 1) -> int:
    """Проставляет места: каждая группа — участники с одинаковым ключом сортировки."""
    place = start
    for g in groups:
        for s in g:
            s.place = place
        place += len(g)
    return place


def _group(items: list[Standing], keyf) -> list[list[Standing]]:
    items = sorted(items, key=keyf)
    out = []
    for s in items:
        if out and keyf(out[-1][0]) == keyf(s):
            out[-1].append(s)
        else:
            out.append([s])
    return out


def _inf(x):
    return x if x is not None else Decimal("1e9")


def jumpoff_candidates(rt: RouteType, standings: list[Standing], stage: str) -> list:
    """Кто проходит в перепрыжку / 2-ю фазу (по уже введённым гитам). stage — ключ следующего гита."""
    if stage in ("round2",) or (stage == "jo1" and rt.key == "two_rounds"):
        return _two_rounds_candidates(rt, standings, stage)
    if stage == "jo1":
        main = [s for s in standings if s.rounds.get("main") and s.rounds["main"].finished]
        if rt.key == "jumpoff_immediate":
            return [s.key for s in main if s.rounds["main"].penalties == 0]
        if not main:
            return []
        best = min(s.rounds["main"].penalties for s in main)
        tied = [s for s in main if s.rounds["main"].penalties == best]
        return [s.key for s in tied] if len(tied) > 1 else []
    if stage == "jo2":
        jo = [s for s in standings if s.rounds.get("jo1") and s.rounds["jo1"].finished]
        if not jo:
            return []
        timed = route_round(rt, "jo1").timed
        keyf = (lambda s: (s.rounds["jo1"].penalties, s.rounds["jo1"].time)) if timed else (lambda s: (s.rounds["jo1"].penalties,))
        best = min(keyf(s) for s in jo)
        tied = [s for s in jo if keyf(s) == best]
        return [s.key for s in tied] if len(tied) > 1 else []
    if stage == "phase2":
        p1 = [s for s in standings if s.rounds.get("phase1") and s.rounds["phase1"].finished]
        if rt.key == "two_phases_special":
            return [s.key for s in p1]
        return [s.key for s in p1 if s.rounds["phase1"].penalties == 0]
    return []


def _two_rounds_candidates(rt, standings, stage):
    if stage == "round2":
        return [s.key for s in standings if s.rounds.get("round1") and s.rounds["round1"].finished]
    both = [s for s in standings if s.rounds.get("round2") and s.rounds["round2"].finished]
    if not both:
        return []
    tot = {s.key: s.rounds["round1"].penalties + s.rounds["round2"].penalties for s in both}
    best = min(tot.values())
    tied = [k for k, v in tot.items() if v == best]
    return tied if len(tied) > 1 else []


def route_round(rt: RouteType, key: str) -> RoundSpec:
    return next(r for r in rt.rounds if r.key == key)


def rank(rt: RouteType, standings: list[Standing], variant: str = "") -> list[Standing]:
    """Проставляет места по формуле маршрута. Не закончившие основной гит мест не получают."""
    if rt.manual:
        return standings
    first = rt.rounds[0].key
    for s in standings:
        r0 = s.rounds.get(first)
        if r0 is None:
            s.status, s.reason = "pending", ""
        elif not r0.finished:
            s.status, s.reason = r0.status, r0.reason
    active = [s for s in standings if s.rounds.get(first) and s.rounds[first].finished]

    if rt.key in ("no_time", "clear_speed", "speed_handiness", "choose_route"):
        timed = rt.rounds[0].timed
        keyf = (lambda s: (s.rounds["main"].penalties, _inf(s.rounds["main"].time))) if timed else (lambda s: (s.rounds["main"].penalties,))
        if rt.key in ("speed_handiness", "choose_route"):
            keyf = lambda s: (s.rounds["main"].penalties,)  # noqa: E731 — по таблице C итог уже в секундах
        _assign(_group(active, keyf))
        for s in active:
            s.total = s.rounds["main"].penalties
        return standings

    if rt.key in ("no_time_jo", "no_time_2jo", "jumpoff", "jumpoff_2", "jumpoff_immediate"):
        return _rank_jumpoff(rt, standings, active)

    if rt.key in ("two_phases", "two_phases_special"):
        return _rank_phases(rt, standings, active, variant)
    if rt.key == "two_rounds":
        return _rank_two_rounds(rt, standings, active, variant)
    raise ValueError(f"Нет формулы подсчёта для «{rt.name}»")


def _rank_jumpoff(rt: RouteType, standings, active):
    main_timed = rt.key in ("jumpoff", "jumpoff_2", "jumpoff_immediate")
    jo_keys = [r.key for r in rt.rounds if r.jumpoff]
    in_jo = [s for s in active if any(k in s.rounds for k in jo_keys)]
    rest = [s for s in active if s not in in_jo]
    place = 1
    if in_jo:
        # Участники перепрыжки — выше всех; сначала по последней перепрыжке, затем по предыдущей.
        def jo_key(s):
            out = []
            for k in reversed(jo_keys):
                r = s.rounds.get(k)
                spec = route_round(rt, k)
                if r is None:
                    out.append((1, 0, 0))  # не участвовал в этой перепрыжке — ниже участвовавших
                elif not r.finished:
                    out.append((2, 0, 0))  # сошедшие/исключённые в перепрыжке — последние места перепрыжки (п. 19.3)
                else:
                    out.append((0, r.penalties, _inf(r.time) if spec.timed else 0))
            return tuple(out)

        place = _assign(_group(in_jo, jo_key))
    for s in in_jo:
        last = next((s.rounds[k] for k in reversed(jo_keys) if k in s.rounds), None)
        s.total = last.penalties if last and last.finished else None
    keyf = (lambda s: (s.rounds["main"].penalties, _inf(s.rounds["main"].time))) if main_timed else (lambda s: (s.rounds["main"].penalties,))
    _assign(_group(rest, keyf), place)
    for s in rest:
        s.total = s.rounds["main"].penalties
    return standings


def _rank_phases(rt, standings, active, variant):
    special = rt.key == "two_phases_special"
    p1_timed = variant in ("35.1.5.3", "35.1.5.5")
    p2_timed = special or variant in ("35.1.5.2", "35.1.5.3", "35.1.5.4", "35.1.5.5")
    finished2 = [s for s in active if s.rounds.get("phase2") and s.rounds["phase2"].finished]
    failed2 = [s for s in active if s.rounds.get("phase2") and not s.rounds["phase2"].finished]
    only1 = [s for s in active if "phase2" not in s.rounds]
    if special:
        # 35.2: сумма штрафов фаз, затем время 2-й фазы; не закончившие 2-ю фазу мест не получают.
        for s in finished2:
            s.total = s.rounds["phase1"].penalties + s.rounds["phase2"].penalties
        _assign(_group(finished2, lambda s: (s.total, _inf(s.rounds["phase2"].time))))
        for s in failed2:
            s.status, s.reason, s.place = s.rounds["phase2"].status, s.rounds["phase2"].reason, None
        for s in only1:
            s.status = "pending"
        return standings
    # 35.1: места по 2-й фазе; остановленные после 1-й фазы — ниже, по 1-й фазе.
    k2 = (lambda s: (s.rounds["phase2"].penalties, _inf(s.rounds["phase2"].time))) if p2_timed else (lambda s: (s.rounds["phase2"].penalties,))
    for s in finished2:
        s.total = s.rounds["phase2"].penalties
    place = _assign(_group(finished2, k2))
    if failed2:
        # 35.1.6: исключённые или сошедшие во 2-й фазе занимают одинаковые места после завершивших её
        place = _assign([failed2], place)
    k1 = (lambda s: (s.rounds["phase1"].penalties, _inf(s.rounds["phase1"].time))) if p1_timed else (lambda s: (s.rounds["phase1"].penalties,))
    for s in only1:
        s.total = s.rounds["phase1"].penalties
    _assign(_group(only1, k1), place)
    return standings


def _rank_two_rounds(rt, standings, active, variant):
    """п. 34.4: перепрыжка — по очкам и времени; остальные прошедшие 2-й гит — по сумме штрафов (и времени по формуле);
    не попавшие во 2-й гит — по 1-му гиту. Сошедшие во 2-м гите / перепрыжке — одинаковое последнее место (п. 19.3.1)."""
    r1_timed = round_timed(rt, variant, "round1")
    r2_timed = round_timed(rt, variant, "round2")
    in_jo = [s for s in active if "jo1" in s.rounds]
    jo_ok = [s for s in in_jo if s.rounds["jo1"].finished]
    jo_bad = [s for s in in_jo if not s.rounds["jo1"].finished]
    rest = [s for s in active if s not in in_jo]
    r2_ok = [s for s in rest if s.rounds.get("round2") and s.rounds["round2"].finished]
    r2_bad = [s for s in rest if s.rounds.get("round2") and not s.rounds["round2"].finished]
    only1 = [s for s in rest if "round2" not in s.rounds]
    place = _assign(_group(jo_ok, lambda s: (s.rounds["jo1"].penalties, _inf(s.rounds["jo1"].time))))
    if jo_bad:
        place = _assign([jo_bad], place)
    for s in jo_ok + jo_bad + r2_ok:
        s.total = s.rounds["round1"].penalties + s.rounds["round2"].penalties

    def k2(s):
        if variant == "34.3.1":  # 34.4.1: сумма штрафов и время 1-го гита
            return (s.total, _inf(s.rounds["round1"].time))
        if variant == "34.3.2":  # 34.4.2: только сумма штрафов
            return (s.total,)
        return (s.total, _inf(s.rounds["round2"].time) if r2_timed else 0)  # 34.4.3 / 34.4.4

    place = _assign(_group(r2_ok, k2), place)
    if r2_bad:
        place = _assign([r2_bad], place)
    for s in only1:
        s.total = s.rounds["round1"].penalties
    _assign(_group(only1, lambda s: (s.rounds["round1"].penalties, _inf(s.rounds["round1"].time) if r1_timed else 0)), place)
    return standings


def fmt(x) -> str:
    """Число для протокола: 4, 0,25, 38,41."""
    if x is None:
        return ""
    d = Decimal(x)
    if d == d.to_integral_value():
        return str(int(d))
    return f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}".rstrip("0").rstrip(".").replace(".", ",")
