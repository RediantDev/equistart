"""Ограничения Правил (DIR-04) — для предупреждений в заявке и у судьи, а не для блокировки.

На клубных турнирах организатор вправе устанавливать свои условия, поэтому подача не запрещается.
Источник — Правила вида спорта «Конный спорт» (приказ №1256): гл. X п. 2.2–2.13 (конкур), гл. IX п. 5.7.1 (выездка);
сводка — docs/RULES.md, разделы 1.6 и 2.6.
"""

from collections import defaultdict

# минимальный возраст лошади → максимальная высота 1-го гита (п. 2.3)
HORSE_AGE_HEIGHT = [(4, 120), (5, 130), (6, 140), (7, 150)]
# минимальный возраст всадника → максимальная высота 1-го гита (п. 2.4)
RIDER_AGE_HEIGHT = [(9, 110), (10, 120), (11, 120), (12, 130), (13, 135), (14, 140), (15, 145), (16, 155)]


def max_height_for_horse(age: int) -> int | None:
    if age >= 8:
        return None
    allowed = [h for a, h in HORSE_AGE_HEIGHT if age >= a]
    return max(allowed) if allowed else 0


def max_height_for_rider(age: int) -> int | None:
    if age >= 18:
        return None
    allowed = [h for a, h in RIDER_AGE_HEIGHT if age >= a]
    return max(allowed) if allowed else 0


def jumping_starts_limit(horse_age: int, is_pony: bool, height: int) -> int:
    """Стартов лошади в день (п. 2.13; для пони — п. 2.16.3)."""
    if is_pony:
        return 2 if height >= 105 else 3
    if horse_age <= 5:
        return 2
    return 2 if height >= 115 else 3


def dressage_starts_limit(rider_age: int | None) -> int:
    """Стартов лошади в день в выездке на региональных и ниже (гл. IX п. 5.7.1), по возрастной группе всадника."""
    if rider_age is None or rider_age >= 16:
        return 1
    if rider_age <= 14:
        return 3
    return 2


def _height(line):
    p = line.tclass.program
    if line.height:
        return line.height
    return max(p.heights) if p.heights else None


def entry_warnings(tournament, lines, existing=None) -> list[str]:
    """Предупреждения по набору выступлений заявки (и уже поданных выступлений тех же лошадей)."""
    from apps.entries.models import Entry

    year = (tournament.pub_starts_on or tournament.starts_on).year
    out = []
    seen = set()

    def warn(text):
        if text not in seen:
            seen.add(text)
            out.append(text)

    per_day = defaultdict(list)  # (horse, day, discipline) -> [(line, height)]
    riders = defaultdict(set)  # horse -> {athlete}
    horse_ids = {l.horse.pk for l in lines}
    current = list(lines)
    if horse_ids:
        for e in Entry.objects.filter(tournament=tournament, horse_id__in=horse_ids, decision__in=Entry.ACTIVE).select_related("tclass__program", "athlete", "horse"):
            from apps.entries.services import Line

            current.append(Line(e.athlete, e.horse, e.tclass, e.height))
    keys = set()
    for l in current:
        k = (l.athlete.pk, l.horse.pk, l.tclass.pk, l.height)
        if k in keys:
            continue
        keys.add(k)
        p = l.tclass.program
        per_day[(l.horse.pk, p.day, p.discipline)].append(l)
        riders[l.horse.pk].add(l.athlete.pk)

    for l in lines:
        p = l.tclass.program
        horse_age = year - l.horse.birth_year
        rider_age = year - l.athlete.birth_date.year if l.athlete.birth_date else None
        if p.is_jumping:
            h = _height(l)
            if h:
                mh = max_height_for_horse(horse_age)
                if mh is not None and h > mh:
                    warn(f"«{l.horse.name}» ({horse_age} лет): высота {h} см выше допустимой для этого возраста — до {mh} см (гл. X п. 2.3).")
                if rider_age is not None:
                    mr = max_height_for_rider(rider_age)
                    if mr is not None and h > mr:
                        warn(f"«{l.athlete.public_name}» ({rider_age} лет): высота {h} см выше допустимой для этого возраста — до {mr} см (гл. X п. 2.4).")
            if rider_age is not None and 9 <= rider_age <= 15 and horse_age < 6:
                warn(f"«{l.athlete.public_name}» ({rider_age} лет) на лошади младше 6 лет — не допускается (гл. X п. 2.8).")
            if p.program_type == "manual" and "дерби" in p.name.lower() and rider_age is not None and rider_age < 18:
                warn(f"«{l.athlete.public_name}»: в дерби — только всадники 18 лет и старше (гл. X п. 2.6).")

    for (horse_id, day, disc), items in per_day.items():
        if not any(l.horse.pk == horse_id for l in lines):
            continue
        horse = items[0].horse
        horse_age = year - horse.birth_year
        n = len(items)
        if disc == "jumping":
            top = max((_height(l) or 0) for l in items)
            limit = jumping_starts_limit(horse_age, horse.is_pony, top)
            if n > limit:
                warn(f"«{horse.name}»: {n} стартов {day:%d.%m} при высоте до {top} см — по Правилам не более {limit} в день (гл. X п. 2.13).")
        elif disc == "dressage":
            ages = [year - l.athlete.birth_date.year for l in items if l.athlete.birth_date]
            limit = dressage_starts_limit(min(ages) if ages else None)
            if n > limit:
                warn(f"«{horse.name}»: {n} езды {day:%d.%m} — по Правилам не более {limit} в день для этой возрастной группы (гл. IX п. 5.7.1).")

    for horse_id, athletes in riders.items():
        if len(athletes) < 2 or not any(l.horse.pk == horse_id for l in lines):
            continue
        horse = next(l.horse for l in current if l.horse.pk == horse_id)
        heights = [(_height(l) or 0) for l in current if l.horse.pk == horse_id and l.tclass.program.is_jumping]
        if not heights:
            continue
        top = max(heights)
        horse_age = year - horse.birth_year
        limit = 1 if (top >= 130 or horse_age <= 5) else 2 if top > 110 else 3
        if len(athletes) > limit:
            warn(f"«{horse.name}»: всадников на турнире — {len(athletes)}, при высоте {top} см допускается не более {limit} (гл. X п. 2.12).")
    return out
