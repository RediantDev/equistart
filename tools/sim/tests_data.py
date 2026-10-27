"""Езды для турниров по выездке. Тексты упражнений — обобщённые формулировки для теста, не копии бланков."""

def movements(spec):
    """spec: [(буквы, описание, коэф)] -> текст для конструктора «№ | буквы | упражнение | коэф»."""
    return "\n".join(f"{i + 1} | {l} | {d} | {k}" for i, (l, d, k) in enumerate(spec))


BASIC = [("A–X", "Въезд рабочим галопом, остановка, приветствие", 1), ("C", "Ездой налево, рабочая рысь", 1),
         ("HXF", "Перемена направления, удлинение шагов на рыси", 1), ("A", "Вольт 10 м", 1),
         ("KXM", "Перемена направления, рабочая рысь", 1), ("C", "Переход в средний шаг", 1),
         ("HXF", "Свободный шаг на длинном поводе", 2), ("F", "Средний шаг", 1), ("A", "Подъём в рабочий галоп с правой ноги", 1),
         ("E", "Круг 20 м", 1), ("MXK", "Перемена направления, переход на рысь у X", 1), ("A", "Подъём в рабочий галоп с левой ноги", 1),
         ("B", "Круг 15 м", 1), ("HXF", "Удлинение шагов на галопе", 1), ("A", "Рабочая рысь", 1),
         ("X", "Остановка, неподвижность, приветствие", 1)]
COLLECTIVE_STD = "Аллюры (свобода и правильность) | 1\nИмпульс | 1\nПодчинение | 2\nПосадка и средства управления | 2"
COLLECTIVE_FEI = "Общее впечатление | 10"


def ext(base_list, extra):
    return base_list + extra


YOUTH_PRELIM = ext(BASIC, [("KXM", "Прибавленная рысь", 2), ("C", "Плечом внутрь налево", 1), ("B", "Принимание направо", 1),
                           ("M", "Плечом внутрь направо", 1), ("E", "Принимание налево", 1), ("A", "Простая смена ноги", 1),
                           ("F–X", "Серпантин в три петли с контргалопом", 1), ("X", "Пируэт в шаге", 1)])
YOUTH_TEAM = ext(YOUTH_PRELIM, [("HXF", "Смена ноги на галопе в воздухе", 1), ("D", "Полупируэт налево", 2), ("G", "Полупируэт направо", 2)])
SMALL_PRIZE = ext(YOUTH_TEAM, [("KXM", "Смена ноги через 4 темпа (5 раз)", 1), ("HXF", "Смена ноги через 3 темпа (5 раз)", 1),
                               ("MXK", "Прибавленный галоп", 1)])
MEDIUM_1 = ext(SMALL_PRIZE, [("KXM", "Смена ноги через 2 темпа (9 раз)", 1), ("D", "Пируэт налево", 2), ("G", "Пируэт направо", 2)])
GRAND = ext(MEDIUM_1, [("HXF", "Пассаж", 2), ("X", "Пиаффе 12–15 темпов", 2), ("KXM", "Смена ноги каждый темп (15 раз)", 1),
                       ("C", "Переходы пассаж — пиаффе — пассаж", 1)])
CHILD_TECH = BASIC
CHILD_QUALITY = "Посадка всадника | 2\nПравильность применения средств управления | 2\nТочность выполнения схемы | 1"

DRESSAGE_TESTS = {
    "pp_youth": dict(name="Предварительный приз. Юноши (тест)", short="ППЮ", level="Юноши", kind="standard", arena="20x60", minutes=6,
                     movements=movements(YOUTH_PRELIM), collective=COLLECTIVE_STD, scheme="fei_junior", tie="collective"),
    "kp_youth": dict(name="Командный приз. Юноши (тест)", short="КПЮ", level="Юноши", kind="standard", arena="20x60", minutes=6,
                     movements=movements(YOUTH_TEAM), collective=COLLECTIVE_STD, scheme="fei_junior", tie="collective"),
    "mp": dict(name="Малый приз (тест)", short="МП", level="Средний", kind="standard", arena="20x60", minutes=6,
               movements=movements(SMALL_PRIZE), collective=COLLECTIVE_FEI, scheme="fei_senior", tie="collective"),
    "sp1": dict(name="Средний приз №1 (тест)", short="СП1", level="Средний", kind="standard", arena="20x60", minutes=7,
                movements=movements(MEDIUM_1), collective=COLLECTIVE_FEI, scheme="fei_senior", tie="collective"),
    "bp": dict(name="Большой приз (тест)", short="БП", level="Высший", kind="standard", arena="20x60", minutes=8,
               movements=movements(GRAND), collective=COLLECTIVE_FEI, scheme="fei_senior", tie="collective"),
    "child": dict(name="Детская езда (тест)", short="ДЕ", level="Дети", kind="children", arena="20x40", minutes=5,
                  movements=movements(CHILD_TECH), collective=COLLECTIVE_STD, second=CHILD_QUALITY, scheme="fei_junior", tie="collective"),
}
