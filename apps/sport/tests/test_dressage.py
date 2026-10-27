"""Эталонные расчёты выездки: технические результаты, присланные заказчиком (docs/RULES.md, 2.7)."""

from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.sport import dressage as S


def make_test(units: int, kind="standard", scheme="", tie="collective"):
    """Езда с максимумом units × 10 баллов: units − 1 упражнений × 1 и общая оценка × 1."""
    part = {"key": "technique", "title": "Техника", "step": "0.5",
            "movements": [{"n": i + 1, "text": f"Упражнение {i + 1}", "coef": 1} for i in range(units - 1)],
            "collective": [{"text": "Гармония", "coef": 1}]}
    return {"kind": kind, "parts": [part], "penalty_scheme": scheme, "tie_rule": tie}


def marks_for(points, n):
    """Оценки (шаг 0,5), дающие нужную сумму баллов на n пунктах с коэффициентом 1."""
    halves = int(D(str(points)) * 2)
    base, extra = divmod(halves, n)
    return [D(base + (1 if i < extra else 0)) / 2 for i in range(n)]


def judges(test, points_by_pos):
    n = len(S.items_of(test["parts"][0]))
    return [S.judge_score(test, pos, "all", {"technique": marks_for(p, n)}, 0, 0) for pos, p in points_by_pos]


class ViVatSP1(SimpleTestCase):
    """«Виват, Россия!», 09.05.2026, Средний приз №1 (максимум 340), 5 судей E, H, C, M, B."""

    def test_row_1(self):
        t = make_test(34)
        js = judges(t, [("E", "228.0"), ("H", "232.0"), ("C", "228.0"), ("M", "231.5"), ("B", "215.0")])
        self.assertEqual([S.q3(j.pct) for j in js], [D("67.059"), D("68.235"), D("67.059"), D("68.088"), D("63.235")])
        r = S.pair_result(t, js, 0, 0)
        self.assertEqual((r.total_points, S.q3(r.pct)), (D("1134.5"), D("66.735")))

    def test_ranking_and_judge_places(self):
        t = make_test(34)
        rows = [
            ("Приданцева", S.pair_result(t, judges(t, [("E", 236), ("H", "242.5"), ("C", 236), ("M", 236), ("B", "240.5")]), 0, 0)),
            ("Волкова", S.pair_result(t, judges(t, [("E", "225.5"), ("H", 227), ("C", 234), ("M", 229), ("B", "228.5")]), 0, 0)),
            ("Ульева", S.pair_result(t, judges(t, [("E", "224.5"), ("H", "228.5"), ("C", "229.5"), ("M", 228), ("B", 225)]), 0, 0)),
        ]
        self.assertEqual([S.q3(r.pct) for _, r in rows], [D("70.059"), D("67.294"), D("66.794")])
        self.assertEqual(S.rank(rows, "collective"), {"Приданцева": 1, "Волкова": 2, "Ульева": 3})
        self.assertEqual(S.judge_places(rows, "H"), {"Приданцева": 1, "Ульева": 2, "Волкова": 3})


class MaximaGP(SimpleTestCase):
    """«Maxima Masters», 14.06.2025, Большой приз (максимум 460)."""

    def test_rows(self):
        t = make_test(46)
        r1 = S.pair_result(t, judges(t, [("E", "339.5"), ("H", "329.5"), ("C", "325.5"), ("M", 327), ("B", "328.5")]), 0, 0)
        r15 = S.pair_result(t, judges(t, [("E", 286), ("H", 276), ("C", 280), ("M", "275.5"), ("B", "279.5")]), 0, 0)
        self.assertEqual((S.q3(r1.pct), r1.total_points), (D("71.739"), D("1650.0")))
        self.assertEqual((S.q3(r15.pct), r15.total_points), (D("60.739"), D("1397.0")))


class PenaltyTests(SimpleTestCase):
    def test_fei_senior(self):
        t = make_test(34, scheme="fei_senior")
        n = len(S.items_of(t["parts"][0]))
        j = S.judge_score(t, "C", "all", {"technique": marks_for(228, n)}, 1, 0)
        self.assertEqual(S.q3(j.pct), D("65.059"))  # −2 % у каждого судьи
        self.assertTrue(S.pair_result(t, [j], 2, 0).eliminated)

    def test_fei_junior_and_points(self):
        self.assertEqual(S.penalties("fei_junior", 2, 1), (D(0), D("2.0"), False))  # 0,5 + 1 + 0,5
        self.assertTrue(S.penalties("fei_junior", 3, 0)[2])
        self.assertEqual(S.penalties("fksr_points", 2, 1), (D(8), D(0), False))  # 2 + 4 + 2 балла
        with self.assertRaises(ValueError):
            S.penalties("", 1, 0)  # схема не выбрана — штраф применить нельзя

    def test_mark_step(self):
        self.assertEqual(S.check_mark("7,5", "0.5"), D("7.5"))
        with self.assertRaises(ValueError):
            S.check_mark("7.3", "0.5")
        self.assertEqual(S.check_mark("7.3", "0.1"), D("7.3"))
        with self.assertRaises(ValueError):
            S.check_mark("11", "0.5")

    def test_children_technique_and_quality(self):
        t = {"kind": "children", "penalty_scheme": "fei_junior", "tie_rule": "share", "parts": [
            {"key": "technique", "step": "0.5", "movements": [{"text": "a", "coef": 1}, {"text": "b", "coef": 2}], "collective": []},
            {"key": "quality", "step": "0.1", "movements": [], "collective": [{"text": "Посадка", "coef": 1}, {"text": "Точность", "coef": 1}]},
        ]}
        tech = S.judge_score(t, "C", "technique", {"technique": [7, 8]}, 0, 0)  # 23/30
        q1 = S.judge_score(t, "E", "quality", {"quality": [D("7.2"), D("6.8")]}, 0, 0)  # 14/20
        r = S.pair_result(t, [tech, q1], 0, 0)
        self.assertEqual(S.q3(r.pct), S.q3((D(23) / 30 * 100 + D(70)) / 2))

    def test_validate(self):
        with self.assertRaises(ValueError):
            S.validate_parts("freestyle", [{"key": "technique", "movements": [{"text": "x", "coef": 1}]}])
