"""Эталонные расчёты конкура: таблицы п. 16 и присланные заказчиком технические результаты (docs/RULES.md, 1.6а)."""

from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.sport import jumping as J


def rnd(table, spec, **kw):
    return J.score_round(table, spec, J.RoundInput(**{k: v for k, v in kw.items() if k not in ("ta", "limit", "indoor")}),
                         time_allowed=kw.get("ta"), limit=kw.get("limit"), indoor=kw.get("indoor", False))


class TableTests(SimpleTestCase):
    def test_table_a(self):
        r = rnd("A", J.MAIN, knockdowns=2, refusals=1, time="75.20", ta=72, limit=144)
        self.assertEqual((r.obstacle_pen, r.time_pen, r.penalties), (D(12), D(4), D(16)))  # 3,2 с сверх нормы → 4 очка
        self.assertEqual(rnd("A", J.MAIN, refusals=2, time="70").status, "eliminated")
        self.assertEqual(rnd("A", J.MAIN, time="145", ta=72, limit=144).status, "eliminated")
        self.assertEqual(rnd("A", J.MAIN, fall=True).status, "eliminated")

    def test_table_b1_time_per_started_4_seconds(self):
        self.assertEqual(rnd("B1", J.MAIN, time="77.01", ta=72).time_pen, D(2))  # 5,01 с → 2 очка
        self.assertEqual(rnd("B1", J.MAIN, time="76", ta=72).time_pen, D(1))
        # в перепрыжке с учётом времени — 1 очко за секунду
        self.assertEqual(rnd("B1", J.JO1_T, time="40.5", ta=38).time_pen, D(3))

    def test_table_b2(self):
        self.assertEqual(rnd("B2", J.MAIN, refusals=1, time="60", ta=70).penalties, D(3))
        self.assertEqual(rnd("B2", J.MAIN, refusals=2, time="60", ta=70).penalties, D(6))
        self.assertEqual(rnd("B2", J.MAIN, refusals=3, time="60", ta=70).status, "eliminated")
        self.assertEqual(rnd("B2", J.MAIN, time="72.3", ta=70).time_pen, D("0.75"))
        self.assertIsNone(J.table_allowed("B2", 120, True))
        self.assertIsNotNone(J.table_allowed("B2", 130, True))

    def test_table_c(self):
        r = rnd("C", J.MAIN_T, knockdowns=2, corrections=1, time="61.00")
        self.assertEqual((r.time, r.penalties), (D("67.00"), D("75.00")))  # +6 с коррекции, 2 × 4 с
        self.assertEqual(rnd("C", J.MAIN_T, knockdowns=1, time="60", indoor=True).penalties, D(63))
        self.assertEqual(rnd("C", J.MAIN_T, refusals=2, time="60").status, "eliminated")
        self.assertEqual(J.time_limit("C", None, 500), D(120))
        self.assertEqual(J.time_limit("C", None, 650), D(180))
        self.assertEqual(rnd("C", J.MAIN_T, time="121", limit=J.time_limit("C", None, 500)).status, "eliminated")

    def test_time_allowed_suggestion(self):
        self.assertEqual(J.suggest_time_allowed(420, 350), 72)
        self.assertEqual(J.suggest_time_allowed(421, 350), 73)


def st(key, **rounds):
    s = J.Standing(key)
    for k, r in rounds.items():
        s.rounds[k] = r
    return s


def ok(pen, time=None):
    return J.RoundResult(penalties=D(str(pen)), time=D(str(time)) if time is not None else None)


def bad(status="eliminated"):
    return J.RoundResult(status=status, reason=status)


class ProtocolImmediateJumpoff(SimpleTestCase):
    """Квалификация к Любительскому кубку, 25.07.2026: маршрут 115 см, п. 16.4.2.2, 19.1.3, табл. B1."""

    def test_places(self):
        rt = J.route("jumpoff_immediate")
        rows = [
            st("Марченко", main=ok(0, 60), jo1=ok(0, "35.55")),
            st("Бобровникова", main=ok(0, 61), jo1=ok(0, "37.94")),
            st("Коннова", main=ok(0, 59), jo1=ok(0, "39.43")),
            st("Петрова", main=ok(0, 62), jo1=ok(0, "42.77")),
            st("Прокофьева", main=ok(4, "70.86")),
            st("Карапетян", main=ok(4, "71.59")),
            st("Савинова", main=ok(10, "99.33")),
            st("Попова", main=ok(12, "64.00")),
            st("Кучерявенко", main=bad()),
            st("Бойкова", main=bad("retired")),
        ]
        self.assertEqual(J.jumpoff_candidates(rt, rows, "jo1"), ["Марченко", "Бобровникова", "Коннова", "Петрова"])
        J.rank(rt, rows)
        self.assertEqual([s.place for s in rows], [1, 2, 3, 4, 5, 6, 7, 8, None, None])
        self.assertEqual((rows[8].status, rows[9].status), ("eliminated", "retired"))


class ProtocolTwoPhasesSpecial(SimpleTestCase):
    """MaximaPark, 19.09.2026: маршрут 80 см, специальный «В две фазы», п. 35.2, табл. B2."""

    def test_places_80cm(self):
        rt = J.route("two_phases_special")
        rows = [
            st("Валецкая/Фарфор", phase1=ok(0), phase2=ok(0, "33.62")),
            st("Валецкая/Экватор", phase1=ok(0), phase2=ok(0, "37.38")),
            st("Синявская", phase1=ok(4), phase2=ok(0, "32.82")),
            st("Минасян", phase1=ok(4), phase2=ok(0, "37.21")),
            st("Емельянова", phase1=ok(4), phase2=ok("1.5", "43.09")),
            st("Лушникова", phase1=ok(8), phase2=ok(0, "31.88")),
            st("Халилуллина", phase1=ok(4), phase2=ok(4, "33.48")),
            st("Кислова", phase1=ok(4), phase2=ok("9.75", "66.39")),
            st("Комарова", phase1=bad()),
        ]
        self.assertEqual(len(J.jumpoff_candidates(rt, rows, "phase2")), 8)
        J.rank(rt, rows)
        self.assertEqual([s.place for s in rows], [1, 2, 3, 4, 5, 6, 7, 8, None])
        self.assertEqual([J.fmt(s.total) for s in rows[:8]], ["0", "0", "4", "4", "5,5", "8", "8", "13,75"])

    def test_phase2_time_penalty_b2_is_quarter_point(self):
        # во 2-й фазе (на время) штраф за время по B2 — 0,25 за секунду; 1 очко за секунду — только в перепрыжке
        spec = J.route("two_phases_special").rounds[1]
        self.assertEqual(rnd("B2", spec, time="45.2", ta=40).time_pen, D("1.5"))


class JumpoffTests(SimpleTestCase):
    def test_jumpoff_only_for_tie_on_first_place(self):
        rt = J.route("jumpoff")
        rows = [st("a", main=ok(0, 70)), st("b", main=ok(4, 60))]
        self.assertEqual(J.jumpoff_candidates(rt, rows, "jo1"), [])
        J.rank(rt, rows)
        self.assertEqual([s.place for s in rows], [1, 2])

    def test_jumpoff_retired_shares_last_place_of_jumpoff(self):
        rt = J.route("jumpoff")
        rows = [st("a", main=ok(0, 70), jo1=ok(4, 35)), st("b", main=ok(0, 72), jo1=bad("retired")),
                st("c", main=ok(0, 69), jo1=ok(0, 40)), st("d", main=ok(4, 65))]
        J.rank(rt, rows)
        self.assertEqual({s.key: s.place for s in rows}, {"c": 1, "a": 2, "b": 3, "d": 4})

    def test_no_time_shares_places(self):
        rt = J.route("no_time")
        rows = [st("a", main=ok(0, 70)), st("b", main=ok(0, 60)), st("c", main=ok(4, 50))]
        J.rank(rt, rows)
        self.assertEqual([s.place for s in rows], [1, 1, 3])

    def test_clear_speed(self):
        rt = J.route("clear_speed")
        rows = [st("a", main=ok(4, 50)), st("b", main=ok(0, 61)), st("c", main=ok(0, 60))]
        J.rank(rt, rows)
        self.assertEqual({s.key: s.place for s in rows}, {"c": 1, "b": 2, "a": 3})

    def test_two_phases_classic(self):
        rt = J.route("two_phases")
        rows = [st("a", phase1=ok(0), phase2=ok(0, 30)), st("b", phase1=ok(0), phase2=ok(4, 25)),
                st("c", phase1=ok(4)), st("d", phase1=ok(8))]
        self.assertEqual(J.jumpoff_candidates(rt, rows, "phase2"), ["a", "b"])
        J.rank(rt, rows, "35.1.5.2")
        self.assertEqual([s.place for s in rows], [1, 2, 3, 4])


class TwoRoundsAndPhases(SimpleTestCase):
    def test_two_rounds_sum_then_round2_time(self):
        rt = J.route("two_rounds")
        rows = [st("a", round1=ok(0, 70), round2=ok(4, 60)), st("b", round1=ok(4, 71), round2=ok(0, 58)),
                st("c", round1=ok(0, 69), round2=ok(0, 61)), st("d", round1=ok(8, 72)), st("e", round1=ok(0, 66), round2=bad())]
        self.assertEqual(sorted(J.jumpoff_candidates(rt, rows, "round2")), ["a", "b", "c", "d", "e"])
        J.rank(rt, rows, "34.3.3.2")
        self.assertEqual({s.key: s.place for s in rows}, {"c": 1, "b": 2, "a": 3, "e": 4, "d": 5})

    def test_two_rounds_jumpoff_for_first_place(self):
        rt = J.route("two_rounds")
        rows = [st("a", round1=ok(0, 70), round2=ok(0, 60)), st("b", round1=ok(0, 71), round2=ok(0, 58)), st("c", round1=ok(4, 60), round2=ok(0, 55))]
        self.assertEqual(sorted(J.jumpoff_candidates(rt, rows, "jo1")), ["a", "b"])
        rows[0].rounds["jo1"], rows[1].rounds["jo1"] = ok(0, 40), ok(4, 35)
        J.rank(rt, rows, "34.3.4.2")
        self.assertEqual([s.place for s in rows], [1, 2, 3])

    def test_35_1_failed_in_phase2_share_place(self):
        rt = J.route("two_phases")
        rows = [st("a", phase1=ok(0), phase2=ok(0, 30)), st("b", phase1=ok(0), phase2=bad()),
                st("c", phase1=ok(0), phase2=bad("retired")), st("d", phase1=ok(4))]
        J.rank(rt, rows, "35.1.5.2")
        self.assertEqual([s.place for s in rows], [1, 2, 2, 4])

    def test_35_1_5_4_phase2_table_c(self):
        rt = J.route("two_phases")
        self.assertEqual(J.table_for(rt, "35.1.5.4", "A", "phase2"), "C")
        spec = rt.rounds[1]
        self.assertEqual(rnd("C", spec, knockdowns=1, time="30").penalties, D(33))  # во 2-й фазе повал — 3 с
