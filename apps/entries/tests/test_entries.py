import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Notification
from apps.entries import services
from apps.entries.models import Application, ChangeProposal, Entry
from apps.profiles.models import Athlete, AthleteDelegate, Horse, HorseDocument, Offer
from apps.profiles.services import can_view_horse_documents
from apps.tournaments import services as tservices
from apps.tournaments.models import ProgramItem, TeamMember, Tournament, TournamentClass, Venue

PDF = b"%PDF-1.4\n%%EOF\n"
TMP = tempfile.mkdtemp()


def mkuser(phone, **kw):
    d = {"last_name": "Тест", "first_name": "Юзер"}
    d.update(kw)
    return User.objects.create_user(phone, "Konkur-2026!", **d)


@override_settings(MEDIA_ROOT=TMP)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.org = mkuser("+79990001001", is_organizer=True)
        self.mod = mkuser("+79990001002", is_moderator=True)
        self.coach = mkuser("+79990001003", is_coach=True, last_name="Соколова", first_name="Мария")
        self.other = mkuser("+79990001004", is_coach=True)
        self.kid = mkuser("+79990001005", is_athlete=True, last_name="Смирнова", first_name="Лиза")
        self.judge = mkuser("+79990001006")
        self.start = timezone.localdate() + datetime.timedelta(days=20)
        self.t = self.make_tournament()
        TeamMember.objects.create(tournament=self.t, phone=self.judge.phone, user=self.judge, role="judge", state="accepted", invited_by=self.org)
        self.p1 = self.t.programs.get(name="Маршрут №1")
        self.c_kids = self.p1.classes.get(name="Дети")
        self.c_open = self.p1.classes.get(name="Открытый")
        now = timezone.now()
        self.a1 = Athlete.objects.create(last_name="Смирнова", first_name="Лиза", birth_date=datetime.date(self.start.year - 12, 5, 1), manager=self.coach, consent_confirmed_at=now, rank_jumping="2", rank_dressage="2")
        self.a2 = Athlete.objects.create(last_name="Орлов", first_name="Дима", birth_date=datetime.date(1996, 1, 1), manager=self.coach, consent_confirmed_at=now)
        self.h1 = Horse.objects.create(name="Барон", birth_year=2014, color="bay", sex="gelding", breed="ганноверская", manager=self.coach)
        self.h2 = Horse.objects.create(name="Ладога", birth_year=2013, color="black", sex="mare", breed="тракененская", manager=self.other)

    def make_tournament(self):
        v = Venue.objects.create(name="КСК", region="mos", settlement="Н", timezone="Europe/Moscow", created_by=self.org)
        t = Tournament.objects.create(owner=self.org, title="Кубок", starts_on=self.start, ends_on=self.start, venue=v, timezone="Europe/Moscow",
                                      contact_name="КСК", contact="+7 900 000-00-00", entry_deadline=self.start - datetime.timedelta(days=2))
        t.regulation.save("p.pdf", SimpleUploadedFile("p.pdf", PDF))
        p = ProgramItem.objects.create(tournament=t, day=self.start, discipline="jumping", name="Маршрут №1", program_type="manual", result_fields=["penalties"], heights=[80])
        TournamentClass.objects.create(program=p, name="Дети", birth_year_from=self.start.year - 14, birth_year_to=self.start.year - 10)
        TournamentClass.objects.create(program=p, name="Открытый")
        tservices.submit(self.org, t.pk, t.revision)
        t.refresh_from_db()
        tservices.approve(self.mod, t.pk, t.revision)
        t.refresh_from_db()
        return t

    def submit(self, user, lines, key="k1", late=False):
        return services.submit(user, self.t.pk, lines, key, late=late)


class ProfileTests(Base):
    def test_athlete_requires_only_name_and_consent(self):
        self.client.force_login(self.coach)
        resp = self.client.post(reverse("profiles:athlete_new"), {"last_name": "Ким", "first_name": "Алина"})
        self.assertContains(resp, "Без согласия")
        resp = self.client.post(reverse("profiles:athlete_new"), {"last_name": "Ким", "first_name": "Алина", "consent": "on"})
        a = Athlete.objects.get(last_name="Ким")
        self.assertRedirects(resp, reverse("profiles:athlete", args=[a.pk]))
        self.assertEqual(a.manager, self.coach)
        self.assertIsNotNone(a.consent_confirmed_at)

    def test_same_names_not_merged(self):
        self.client.force_login(self.other)
        self.client.post(reverse("profiles:athlete_new"), {"last_name": "Смирнова", "first_name": "Лиза", "consent": "on"})
        self.assertEqual(Athlete.objects.filter(last_name="Смирнова").count(), 2)

    def test_foreign_athlete_closed(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("profiles:athlete", args=[self.a1.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("profiles:athlete_edit", args=[self.a1.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("profiles:athlete_action", args=[self.a1.pk, "transfer"]), {"phone": self.other.phone}).status_code, 403)

    def test_delegate_can_enter_but_not_edit(self):
        AthleteDelegate.objects.create(athlete=self.a1, user=self.other, granted_by=self.coach)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("profiles:athlete", args=[self.a1.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("profiles:athlete_edit", args=[self.a1.pk])).status_code, 403)

    def test_link_requires_same_phone(self):
        self.client.force_login(self.coach)
        self.client.post(reverse("profiles:athlete_action", args=[self.a1.pk, "link"]), {"phone": self.kid.phone})
        offer = Offer.objects.get()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("profiles:offer_respond", args=[offer.pk]), {"decision": "accept"}).status_code, 403)
        self.client.force_login(self.kid)
        self.client.post(reverse("profiles:offer_respond", args=[offer.pk]), {"decision": "accept"})
        self.a1.refresh_from_db()
        self.assertEqual(self.a1.linked_user, self.kid)
        self.assertEqual(self.a1.manager, self.coach)

    def test_horse_fksr_unique_and_similar_warning(self):
        self.client.force_login(self.coach)
        data = {"name": "Гром", "birth_year": 2015, "color": "grey", "sex": "stallion", "breed": "орловская", "fksr_number": "ab 123"}
        self.client.post(reverse("profiles:horse_new"), data)
        self.assertEqual(Horse.objects.get(name="Гром").fksr_number, "AB123")
        resp = self.client.post(reverse("profiles:horse_new"), {**data, "name": "Гром-2", "fksr_number": "AB-123"})
        self.assertContains(resp, "уже есть в сервисе")
        resp = self.client.post(reverse("profiles:horse_new"), {**data, "fksr_number": ""})
        self.assertContains(resp, "с такой кличкой и тем же годом рождения")
        self.assertEqual(Horse.objects.filter(name="Гром").count(), 1)
        self.client.post(reverse("profiles:horse_new"), {**data, "fksr_number": "", "confirm_similar": "1"})
        self.assertEqual(Horse.objects.filter(name="Гром").count(), 2)
        # та же кличка, другой год — обычная тёзка, без предупреждения и лишнего шага
        resp = self.client.post(reverse("profiles:horse_new"), {**data, "fksr_number": "", "birth_year": 2010})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Horse.objects.filter(name="Гром").count(), 3)

    def test_future_birth_year_rejected(self):
        self.client.force_login(self.coach)
        resp = self.client.post(reverse("profiles:horse_new"), {"name": "Х", "birth_year": timezone.localdate().year + 1, "color": "grey", "sex": "mare", "breed": "x"})
        self.assertContains(resp, "из будущего")

    def test_horse_transfer(self):
        self.client.force_login(self.coach)
        self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "transfer"]), {"phone": self.other.phone})
        offer = Offer.objects.get()
        self.client.force_login(self.other)
        self.client.post(reverse("profiles:offer_respond", args=[offer.pk]), {"decision": "accept"})
        self.h1.refresh_from_db()
        self.assertEqual(self.h1.manager, self.other)
        code = self.h1.code
        self.client.force_login(self.coach)
        self.assertEqual(self.client.get(reverse("profiles:horse_edit", args=[self.h1.pk])).status_code, 403)
        self.assertEqual(Horse.objects.get(pk=self.h1.pk).code, code)

    def test_horse_search_public_fields_only(self):
        self.h2.owner_name = "Секретов С.С."
        self.h2.save()
        self.client.force_login(self.coach)
        resp = self.client.get(reverse("profiles:horse_search"), {"q": "лад"})
        self.assertContains(resp, "Ладога")
        self.assertNotContains(resp, "Секретов")
        resp = self.client.get(reverse("profiles:horse_search"), {"q": self.h2.code, "mode": "code"})
        self.assertContains(resp, "Ладога")

    def test_horse_documents_access(self):
        doc = HorseDocument.objects.create(horse=self.h1, kind="passport", file=SimpleUploadedFile("d.pdf", PDF), content_type="application/pdf", uploaded_by=self.coach)
        url = reverse("profiles:horse_document", args=[self.h1.pk, doc.pk])
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        self.assertTrue(can_view_horse_documents(self.judge, self.h1))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_document_type_checked(self):
        self.client.force_login(self.coach)
        bad = SimpleUploadedFile("x.pdf", b"MZ\x90\x00", "application/pdf")
        self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "upload"]), {"kind": "passport", "file": bad})
        self.assertEqual(HorseDocument.objects.count(), 0)


class SubmitTests(Base):
    def test_group_application_without_athlete_accounts(self):
        app = self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids), services.Line(self.a2, self.h1, self.c_open)])
        self.assertEqual(app.entries.count(), 2)
        self.assertTrue(app.is_group)
        e = app.entries.get(athlete=self.a1)
        self.assertEqual(e.decision, Entry.Decision.PENDING)
        self.assertEqual(e.athlete_snapshot["birth_year"], self.a1.birth_year)
        self.assertTrue(Notification.objects.filter(user=self.org).exists())

    def test_idempotent_repeat_and_conflict(self):
        lines = [services.Line(self.a2, self.h1, self.c_open)]
        a = self.submit(self.coach, lines, key="same")
        b = self.submit(self.coach, lines, key="same")
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Application.objects.count(), 1)
        with self.assertRaises(services.Conflict):
            self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids)], key="same")

    def test_age_and_rank_conditions(self):
        with self.assertRaises(ValueError) as cm:
            self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_kids)])
        self.assertIn("г.р.", cm.exception.args[0][0])
        self.a1.birth_date = None
        self.a1.save()
        with self.assertRaises(ValueError) as cm:
            self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids)], key="k2")
        self.assertIn("нужна дата рождения", cm.exception.args[0][0])

    def test_no_right_for_foreign_athlete(self):
        with self.assertRaises(ValueError) as cm:
            self.submit(self.other, [services.Line(self.a2, self.h2, self.c_open)])
        self.assertIn("Нет права", cm.exception.args[0][0])

    def test_any_horse_can_be_selected(self):
        app = self.submit(self.coach, [services.Line(self.a2, self.h2, self.c_open)])
        self.assertEqual(app.entries.get().horse, self.h2)

    def test_height_required_for_multi_height_route(self):
        p = self.c_open.program
        p.heights = [80, 100]
        p.save()
        with self.assertRaises(ValueError) as cm:
            self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        self.assertIn("высоту", cm.exception.args[0][0])
        app = self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open, 100)], key="k2")
        self.assertEqual(app.entries.get().height, 100)

    def test_rules_warnings_do_not_block(self):
        from apps.sport.rules import entry_warnings

        young = Horse.objects.create(name="Малыш", birth_year=self.start.year - 4, color="bay", sex="mare", breed="x", manager=self.coach)
        p = self.c_open.program
        p.heights = [130]
        p.save()
        w = entry_warnings(self.t, [services.Line(self.a2, young, self.c_open)])
        self.assertTrue(any("п. 2.3" in x for x in w))
        self.submit(self.coach, [services.Line(self.a2, young, self.c_open)])  # подача не запрещена

    def test_duplicate_entry_rejected(self):
        self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        with self.assertRaises(ValueError):
            self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)], key="k2")

    def test_deadline_closed_and_judge_late_entry(self):
        self.t.pub_deadline = timezone.localdate() - datetime.timedelta(days=1)
        self.t.save()
        with self.assertRaises(ValueError):
            self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        j_athlete = Athlete.objects.create(last_name="На", first_name="Месте", manager=self.judge, consent_confirmed_at=timezone.now())
        j_horse = Horse.objects.create(name="Местная", birth_year=2010, color="grey", sex="mare", breed="x", manager=self.judge)
        app = self.submit(self.judge, [services.Line(j_athlete, j_horse, self.c_open)], key="late", late=True)
        self.assertTrue(app.late)
        self.assertEqual(app.entries.get().decision, Entry.Decision.ACCEPTED)
        with self.assertRaises(ValueError):
            self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)], key="late2", late=True)

    def test_apply_page_flow(self):
        self.client.force_login(self.coach)
        url = reverse("entries:apply", args=[self.t.pk])
        self.client.post(url, {"action": "pair_add", "athlete": self.a1.pk, "horse": self.h1.pk})
        resp = self.client.get(url)
        self.assertContains(resp, self.c_kids.name)
        self.client.post(url, {"action": "add", "a": self.a1.pk, "h": self.h1.pk, "tclass": self.c_kids.pk})
        resp = self.client.post(url, {"action": "submit", "contact_name": "Мария", "contact_phone": "+7 999 000-10-03"})
        app = Application.objects.get()
        self.assertRedirects(resp, reverse("entries:application", args=[app.pk]))
        # повтор отправки той же корзины не создаёт второй заявки
        self.assertEqual(Application.objects.count(), 1)


class DecisionTests(Base):
    def setUp(self):
        super().setUp()
        self.app = self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids), services.Line(self.a2, self.h1, self.c_open)])
        self.e1 = self.app.entries.get(athlete=self.a1)
        self.e2 = self.app.entries.get(athlete=self.a2)

    def test_independent_decisions(self):
        r = services.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision)], "accept")
        self.assertTrue(r[0].ok)
        r = services.decide(self.judge, self.t.pk, [(self.e2.pk, self.e2.revision)], "reject", "Лимит лошади")
        self.e1.refresh_from_db()
        self.e2.refresh_from_db()
        self.assertEqual(self.e1.decision, Entry.Decision.ACCEPTED)
        self.assertEqual(self.e2.decision, Entry.Decision.REJECTED)
        self.client.force_login(self.coach)
        resp = self.client.get(reverse("entries:application", args=[self.app.pk]))
        self.assertContains(resp, "Принято")
        self.assertContains(resp, "Отклонено")
        self.assertContains(resp, "Лимит лошади")

    def test_reject_requires_reason_and_stale_revision(self):
        with self.assertRaises(ValueError):
            services.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision)], "reject", "")
        r = services.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision + 5)], "accept")
        self.assertFalse(r[0].ok)

    def test_bulk_partial_result_and_foreign_id(self):
        other_t = self.make_tournament()
        c = other_t.programs.first().classes.get(name="Открытый")
        foreign = services.submit(self.coach, other_t.pk, [services.Line(self.a2, self.h1, c)], "kf").entries.get()
        r = services.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision), (foreign.pk, foreign.revision)], "accept")
        self.assertTrue(r[0].ok)
        self.assertFalse(r[1].ok)
        foreign.refresh_from_db()
        self.assertEqual(foreign.decision, Entry.Decision.PENDING)

    def test_only_judge_decides(self):
        with self.assertRaises(Exception):
            services.decide(self.other, self.t.pk, [(self.e1.pk, self.e1.revision)], "accept")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("entries:manage", args=[self.t.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("entries:application", args=[self.app.pk])).status_code, 403)

    def test_cancelled_not_restored_by_accept(self):
        services.cancel_by_representative(self.coach, self.app.pk, [(self.e1.pk, self.e1.revision)])
        self.e1.refresh_from_db()
        r = services.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision)], "accept")
        self.assertFalse(r[0].ok)

    def test_selective_cancel_keeps_others(self):
        r = services.cancel_by_representative(self.coach, self.app.pk, [(self.e1.pk, self.e1.revision)])
        self.assertTrue(r[0].ok)
        self.e2.refresh_from_db()
        self.assertEqual(self.e2.decision, Entry.Decision.PENDING)
        r = services.cancel_by_representative(self.coach, self.app.pk, [(self.e1.pk, self.e1.revision)])
        self.assertFalse(r[0].ok)
        with self.assertRaises(Exception):
            services.cancel_by_representative(self.other, self.app.pk, [(self.e2.pk, self.e2.revision)])

    def test_cancel_via_ui_requires_confirmation(self):
        self.client.force_login(self.coach)
        url = reverse("entries:cancel", args=[self.app.pk])
        resp = self.client.post(url, {"entry": [self.e1.pk]})
        self.assertContains(resp, "Отменить выступления?")
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.decision, Entry.Decision.PENDING)
        self.client.post(url, {"entry": [self.e1.pk], f"rev-{self.e1.pk}": self.e1.revision, "confirm": "1"})
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.decision, Entry.Decision.CANCELLED)


class ProposalTests(Base):
    def setUp(self):
        super().setUp()
        self.app = self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        self.e = self.app.entries.get()
        services.decide(self.judge, self.t.pk, [(self.e.pk, self.e.revision)], "accept")
        self.e.refresh_from_db()

    def test_unchanged_proposal_rejected(self):
        with self.assertRaises(ValueError):
            services.propose(self.coach, self.e.pk, athlete=self.a2, horse=self.h1)

    def test_proposal_keeps_old_until_accepted(self):
        p = services.propose(self.coach, self.e.pk, horse=self.h2)
        self.e.refresh_from_db()
        self.assertEqual(self.e.horse, self.h1)
        with self.assertRaises(ValueError):
            services.propose(self.coach, self.e.pk, horse=self.h2)
        services.decide_proposal(self.judge, p.pk, True)
        self.e.refresh_from_db()
        self.assertEqual(self.e.horse, self.h2)
        self.assertEqual(self.e.horse_snapshot["name"], "Ладога")
        self.assertEqual(self.e.decision, Entry.Decision.ACCEPTED)

    def test_proposal_reject_keeps_data_and_foreign_cannot_propose(self):
        p = services.propose(self.coach, self.e.pk, tclass=self.c_kids, athlete=self.a1)
        services.decide_proposal(self.judge, p.pk, False, "нет мест")
        self.e.refresh_from_db()
        self.assertEqual(self.e.athlete, self.a2)
        self.assertEqual(ChangeProposal.objects.get().state, ChangeProposal.State.REJECTED)
        with self.assertRaises(Exception):
            services.propose(self.other, self.e.pk, horse=self.h2)


class LinkedAthleteTests(Base):
    def test_linked_athlete_sees_only_own_entries(self):
        self.a1.linked_user = self.kid
        self.a1.save()
        app = self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids), services.Line(self.a2, self.h1, self.c_open)])
        self.client.force_login(self.kid)
        resp = self.client.get(reverse("entries:my_starts"))
        self.assertContains(resp, "Маршрут №1")
        self.assertContains(resp, "Барон")
        self.assertNotContains(resp, "Орлов")
        self.assertEqual(self.client.get(reverse("entries:application", args=[app.pk])).status_code, 403)

    def test_athlete_submits_for_self(self):
        self.a1.linked_user = self.kid
        self.a1.save()
        app = services.submit(self.kid, self.t.pk, [services.Line(self.a1, self.h1, self.c_kids)], "self")
        self.assertEqual(app.submitted_by, self.kid)


class UniqueNumbersTests(Base):
    """Решение 14.10.2026: дублей нет, если совпадают номера ФКСР или FEI."""

    def test_athlete_fksr_and_fei_unique(self):
        self.a1.fksr_number, self.a1.fei_id = "rus 001", "10012345"
        self.a1.save()
        self.client.force_login(self.other)
        base = {"last_name": "Новая", "first_name": "Запись", "consent": "on"}
        r = self.client.post(reverse("profiles:athlete_new"), {**base, "fksr_number": "RUS-001"})
        self.assertContains(r, "уже есть в сервисе")
        r = self.client.post(reverse("profiles:athlete_new"), {**base, "fei_id": "10012345"})
        self.assertContains(r, "уже есть в сервисе")
        self.assertFalse(Athlete.objects.filter(last_name="Новая").exists())
        self.client.post(reverse("profiles:athlete_new"), {**base, "fksr_number": "RUS-002", "fei_id": "10099999"})
        self.assertTrue(Athlete.objects.filter(last_name="Новая", fksr_number="RUS002").exists())

    def test_horse_fei_unique(self):
        self.h1.fei_id = "104AB12"
        self.h1.save()
        self.client.force_login(self.other)
        data = {"name": "Другая", "birth_year": 2015, "color": "grey", "sex": "mare", "breed": "x", "fei_id": "104ab12"}
        r = self.client.post(reverse("profiles:horse_new"), data)
        self.assertContains(r, "FEI ID 104AB12 уже есть")
        self.assertFalse(Horse.objects.filter(name="Другая").exists())

    def test_db_constraint(self):
        from django.db import IntegrityError, transaction

        self.a1.fksr_number = "X1"
        self.a1.save()
        self.a2.fksr_number = "x-1"
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.a2.save()
