"""Проведение с автоматическим подсчётом: конкур у поля и выездка (судьи на буквах, судья на C, судья-подсчёт)."""

import datetime
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.conduct import scoring, services
from apps.conduct.models import DressageSheet, JudgePosition, JumpRound, Result, ResultPublication, StartSlot
from apps.conduct.tests.test_conduct import ConductBase
from apps.entries import services as eservices
from apps.entries.tests.test_entries import TMP, mkuser
from apps.profiles.models import Athlete, Horse
from apps.sport import dressage as D
from apps.sport.models import DressageTest
from apps.tournaments.models import ProgramItem, TeamMember, TournamentClass


def rnd(knockdowns=0, refusals=0, time="", status="ok", corrections=0, reason=""):
    return {"knockdowns": str(knockdowns), "refusals": str(refusals), "time": str(time), "status": status, "corrections": str(corrections),
            "reason": reason}


@override_settings(MEDIA_ROOT=TMP)
class JumpingTests(ConductBase):
    def setUp(self):
        super().setUp()
        self.p1.program_type, self.p1.table, self.p1.result_fields = "jumpoff", "A", []
        self.p1.save()
        now = timezone.now()
        extra = []
        for i, (last, horse) in enumerate((("Белова", "Вихрь"), ("Петров", "Искра"))):
            a = Athlete.objects.create(last_name=last, first_name="А", birth_date=datetime.date(1990, 1, 1), manager=self.coach, consent_confirmed_at=now)
            h = Horse.objects.create(name=horse, birth_year=2012, color="bay", sex="mare", breed="x", manager=self.coach)
            extra.append(eservices.Line(a, h, self.c_open))
        app = self.submit(self.coach, extra, key="k-extra")
        self.e3, self.e4 = app.entries.order_by("pk")
        self.accept(self.e3, self.e4)
        services.draw(self.judge, self.p1.pk)
        scoring.save_course(self.judge, self.p1, {"main_length": "420", "main_speed": "350", "jo1_length": "300", "jo1_speed": "350"})

    def test_course_suggests_time_allowed_and_limit(self):
        p = scoring.round_params(self.p1, "main")
        self.assertEqual((p["time_allowed"], p["limit"]), (Decimal(72), Decimal(144)))

    def test_jumpoff_flow_places_and_publication(self):
        sv = scoring.save_round
        sv(self.judge, self.e2.pk, "main", rnd(0, 0, "70.10"))
        sv(self.judge, self.e3.pk, "main", rnd(0, 0, "71.55"))
        sv(self.judge, self.e4.pk, "main", rnd(1, 1, "75.20"))  # 8 + 4 за время
        self.assertEqual({e.pk for e in scoring.next_stage_candidates(self.p1, "jo1")}, {self.e2.pk, self.e3.pk})
        with self.assertRaises(ValueError):
            sv(self.judge, self.e4.pk, "jo1", rnd(0, 0, "40"))  # не прошёл в перепрыжку
        sv(self.judge, self.e2.pk, "jo1", rnd(1, 0, "35.00"))
        sv(self.judge, self.e3.pk, "jo1", rnd(0, 0, "38.12"))
        places = {r.entry_id: r.place for r in Result.objects.filter(entry__in=[self.e2, self.e3, self.e4])}
        self.assertEqual(places, {self.e3.pk: 1, self.e2.pk: 2, self.e4.pk: 3})
        self.assertEqual(Result.objects.get(entry=self.e4).values["rounds"]["main"]["pen"], "12")
        # e1 в своём зачёте — отдельная классификация
        sv(self.judge, self.e1.pk, "main", rnd(0, 2, "", "eliminated", reason="второе неповиновение"))
        r1 = Result.objects.get(entry=self.e1)
        self.assertEqual((r1.place, r1.status), (None, Result.Status.ELIMINATED))
        pub = services.publish_results(self.judge, self.c_open.pk)
        self.assertEqual(pub.fields, ["Ш/о", "Время", "Ш/о перепр.", "Время перепр."])
        self.assertEqual([r["place"] for r in pub.rows], [1, 2, 3])
        self.assertEqual(pub.rows[2]["cells"], ["12", "75,20", "", ""])  # время основного гита — у всех пар
        # ручной ввод для автоматического маршрута закрыт
        with self.assertRaises(ValueError):
            services.save_result(self.judge, self.e2.pk, {}, "1", "")

    def test_repeat_with_same_client_key_is_not_duplicated(self):
        a = scoring.save_round(self.judge, self.e2.pk, "main", rnd(0, 0, "70"), "key-1")
        b = scoring.save_round(self.judge, self.e2.pk, "main", rnd(1, 0, "70"), "key-1")
        self.assertEqual((a.pk, b.knockdowns, JumpRound.objects.count()), (b.pk, 0, 1))
        c = scoring.save_round(self.judge, self.e2.pk, "main", rnd(1, 0, "70"), "key-2")  # исправление
        self.assertEqual((c.knockdowns, c.revision), (1, 2))

    def test_withdrawn_slot_gets_status(self):
        slot = StartSlot.objects.get(entry=self.e4)
        services.set_status(self.judge, slot.pk, StartSlot.Status.WITHDRAWN)
        self.assertEqual(Result.objects.get(entry=self.e4).status, Result.Status.WITHDRAWN)

    def test_screens_and_permissions(self):
        live = reverse("conduct:jump_live", args=[self.t.pk, self.p1.pk])
        save = reverse("conduct:jump_save", args=[self.t.pk, self.p1.pk])
        self.assertEqual(self.client.get(live).status_code, 302)  # гость → вход
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(live).status_code, 403)
        self.assertEqual(self.client.post(save, {"entry": self.e2.pk, "stage": "main", **rnd(0, 0, "70")}).status_code, 403)
        self.assertFalse(JumpRound.objects.exists())
        self.client.force_login(self.judge)
        self.assertRedirects(self.client.get(reverse("conduct:live", args=[self.t.pk, self.p1.pk])), live)
        resp = self.client.get(live)
        self.assertContains(resp, "Повалы")
        self.assertContains(resp, "норма 72 с")
        resp = self.client.post(save, {"entry": self.e2.pk, "stage": "main", **rnd(0, 0, "70,5")}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertTrue(resp.json()["ok"])
        resp = self.client.post(save, {"entry": self.e2.pk, "stage": "main", **rnd(0, 0, "abc")}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.client.get(reverse("conduct:program", args=[self.t.pk, self.p1.pk])).status_code, 200)

    def test_two_phases_goes_straight_to_phase2(self):
        p = self.p1
        p.program_type, p.table, p.variant = "two_phases_special", "B2", ""
        p.save()
        self.client.force_login(self.judge)
        resp = self.client.post(reverse("conduct:jump_save", args=[self.t.pk, p.pk]), {"entry": self.e2.pk, "stage": "phase1", **rnd(0, 0, "")})
        self.assertIn("stage=phase2", resp["Location"])
        self.assertIn(f"entry={self.e2.pk}", resp["Location"])


@override_settings(MEDIA_ROOT=TMP)
class DressageTests(ConductBase):
    def setUp(self):
        super().setUp()
        self.test = DressageTest.objects.get(is_official=True, name__icontains="Обязательная")
        p = self.p1
        p.discipline, p.program_type, p.dressage_test, p.test_snapshot, p.heights, p.result_fields = (
            "dressage", "dressage_test", self.test, self.test.snapshot(), [], [])
        p.save()
        self.judge2 = mkuser("+79990001010")
        self.judge3 = mkuser("+79990001011")
        for u in (self.judge2, self.judge3):
            TeamMember.objects.create(tournament=self.t, phone=u.phone, user=u, role="judge", state="accepted", invited_by=self.org)
        services.draw(self.judge, p.pk)
        self.rows = [
            {"letter": "C", "user": str(self.judge.pk), "judge_name": "Кузнецова Е."},
            {"letter": "E", "user": str(self.judge2.pk), "judge_name": "Морозов А."},
            {"letter": "B", "user": str(self.judge3.pk), "judge_name": "Волкова Н."},
            {"letter": "SCORER", "user": str(self.org.pk), "judge_name": "Волков А."},
        ]

    def fill(self, user, letter, entry, mark="7"):
        part = self.test.parts[0]
        for i, _ in enumerate(D.items_of(part)):
            scoring.save_mark(user, entry.pk, letter, part["key"], i, mark)

    def test_panel_rules(self):
        with self.assertRaises(ValueError):
            scoring.save_panel(self.judge, self.p1, self.rows[:3])  # нет судьи-подсчёта
        with self.assertRaises(ValueError):
            scoring.save_panel(self.judge, self.p1, [*self.rows, {"letter": "H", "user": str(self.other.pk)}])  # не из команды
        scoring.save_panel(self.judge, self.p1, self.rows)
        self.assertEqual(JudgePosition.objects.filter(program=self.p1).count(), 4)

    def test_full_cycle(self):
        scoring.save_panel(self.judge, self.p1, self.rows)
        e = self.e2
        self.fill(self.judge, "C", e, "7")
        self.fill(self.judge2, "E", e, "6.5")
        self.fill(self.judge3, "B", e, "8")
        with self.assertRaises(ValueError):
            scoring.save_mark(self.judge2, e.pk, "E", "technique", 0, "6.3")  # шаг 0,5
        # судья не может писать в чужой лист
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            scoring.save_mark(self.judge2, e.pk, "C", "technique", 0, "5")
        scoring.sign(self.judge, e.pk, "C")
        scoring.sign(self.judge2, e.pk, "E")
        self.assertTrue(Result.objects.get(entry=e).values.get("pending"))
        scoring.sign(self.judge3, e.pk, "B")
        r = Result.objects.get(entry=e)
        self.assertEqual(r.values["pct"], "71,667")  # (70 + 65 + 80) / 3
        self.assertEqual(r.values["judges"]["E"]["pct"], "65,000")
        with self.assertRaises(ValueError):
            scoring.save_mark(self.judge, e.pk, "C", "technique", 0, "5")  # подписан
        # ошибка в схеме — судья на C; без выбранной схемы штрафов отметку не принять (Q7)
        with self.assertRaises(ValueError):
            scoring.set_flags(self.judge, e.pk, errors=1)
        self.p1.test_snapshot = {**self.p1.test_snapshot, "penalty_scheme": "fksr_points"}
        self.p1.save()
        scoring.set_flags(self.judge, e.pk, errors=1)
        r.refresh_from_db()
        self.assertNotEqual(r.values["pct"], "71,667")
        # возврат и повторная подпись
        scoring.return_sheet(self.org, e.pk, "E", "проверьте п. 3")
        sh = DressageSheet.objects.get(entry=e, position__letter="E")
        self.assertFalse(sh.signed)
        scoring.save_mark(self.judge2, e.pk, "E", "technique", 0, "7")
        scoring.sign(self.judge2, e.pk, "E")
        with self.assertRaises(ValueError):
            services.publish_results(self.org, self.c_open.pk)  # не утверждено
        scoring.approve(self.org, e.pk)
        pub = services.publish_results(self.org, self.c_open.pk)
        self.assertEqual(pub.fields[:3], ["C: баллы", "C: %", "C: место"])
        self.assertEqual(pub.rows[0]["place"], 1)

    def test_independence_and_screens(self):
        scoring.save_panel(self.judge, self.p1, self.rows)
        self.fill(self.judge, "C", self.e2, "7")
        self.client.force_login(self.judge2)
        self.assertEqual(self.client.get(reverse("conduct:judge_sheet", args=[self.t.pk, self.p1.pk, "C"])).status_code, 403)
        resp = self.client.get(reverse("conduct:judge_sheet", args=[self.t.pk, self.p1.pk, "E"]) + f"?entry={self.e2.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="7"')  # чужие оценки не видны
        self.assertEqual(self.client.get(reverse("conduct:scorer", args=[self.t.pk, self.p1.pk])).status_code, 403)
        self.assertRedirects(self.client.get(reverse("conduct:live", args=[self.t.pk, self.p1.pk])),
                             reverse("conduct:judge_sheet", args=[self.t.pk, self.p1.pk, "E"]))
        resp = self.client.post(reverse("conduct:judge_mark", args=[self.t.pk, self.p1.pk, "E"]),
                                {"entry": self.e2.pk, "part": "technique", "i": "0", "value": "6,5"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertTrue(resp.json()["ok"])
        self.client.force_login(self.org)
        self.assertEqual(self.client.get(reverse("conduct:scorer", args=[self.t.pk, self.p1.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("conduct:panel", args=[self.t.pk, self.p1.pk])).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("conduct:panel", args=[self.t.pk, self.p1.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("conduct:flags", args=[self.t.pk, self.p1.pk]), {"entry": self.e2.pk, "errors": "1"}).status_code, 403)

    def test_participant_sheets_after_publication_only(self):
        url = reverse("conduct:my_sheets", args=[self.e2.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.coach)
        self.assertEqual(self.client.get(url).status_code, 404)  # результаты ещё не опубликованы
