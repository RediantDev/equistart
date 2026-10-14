import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.tournaments import services
from apps.tournaments.models import ProgramItem, TeamMember, Tournament, TournamentClass, Venue

PDF = b"%PDF-1.4\n%test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
TMP_MEDIA = tempfile.mkdtemp()


def user(phone, **extra):
    data = {"last_name": "Тестов", "first_name": "Иван"}
    data.update(extra)
    return User.objects.create_user(phone, "Konkur-2026!", **data)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.org = user("+79990000101", is_organizer=True)
        self.other = user("+79990000102", is_organizer=True)
        self.mod = user("+79990000103", is_moderator=True)
        self.coach = user("+79990000104", is_coach=True)
        self.start = timezone.localdate() + datetime.timedelta(days=30)

    def make_ready(self, owner=None, days=2):
        owner = owner or self.org
        venue = Venue.objects.create(name="КСК «Белая роща»", region="mos", settlement="Берёзки", timezone="Europe/Moscow", created_by=owner)
        t = Tournament.objects.create(
            owner=owner, title="Кубок «Белая роща»", starts_on=self.start, ends_on=self.start + datetime.timedelta(days=days - 1),
            venue=venue, timezone="Europe/Moscow", organizer_name="КСК «Белая роща»",
            entry_deadline=self.start - datetime.timedelta(days=3), cancel_terms="Возврат до срока подачи.",
        )
        t.regulation.save("p.pdf", SimpleUploadedFile("p.pdf", PDF, "application/pdf"))
        p = ProgramItem.objects.create(tournament=t, day=self.start, discipline="jumping", name="Маршрут №1 · 80 см", program_type="jumping_clear_time", result_fields=["penalties", "time"])
        TournamentClass.objects.create(program=p, name="Любители", fee_kopecks=250000)
        return t

    def publish(self, t):
        services.submit(self.org if t.owner_id == self.org.pk else t.owner, t.pk, t.revision)
        t.refresh_from_db()
        services.approve(self.mod, t.pk, t.revision)
        t.refresh_from_db()
        return t


class WizardTests(Base):
    def test_guest_and_non_organizer(self):
        self.assertRedirects(self.client.get(reverse("tournaments:create")), "/login/?next=/organizer/new/")
        self.client.force_login(self.coach)
        resp = self.client.get(reverse("tournaments:create"))
        self.assertRedirects(resp, reverse("accounts:add_role"))

    def test_create_with_new_venue_sets_timezone(self):
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:create"), {
            "title": "Осенний приз", "starts_on": self.start.isoformat(), "multiday": "on",
            "ends_on": (self.start + datetime.timedelta(days=1)).isoformat(),
            "venue_name": "КСК «Озёрный»", "venue_region": "sve", "venue_settlement": "Берёзовский",
            "organizer_name": "КСК «Озёрный»", "contact": "+7 900 000-00-00",
        })
        t = Tournament.objects.get()
        self.assertRedirects(resp, reverse("tournaments:step", args=[t.pk, 2]))
        self.assertEqual(t.timezone, "Asia/Yekaterinburg")
        self.assertEqual(t.ends_on, self.start + datetime.timedelta(days=1))
        self.assertEqual(t.status, Tournament.Status.DRAFT)
        self.assertFalse(t.venue.confirmed)

    def test_yakutia_requires_zone(self):
        self.client.force_login(self.org)
        data = {"title": "Якутия", "starts_on": self.start.isoformat(), "venue_name": "Клуб", "venue_region": "sa",
                "venue_settlement": "Якутск", "organizer_name": "Клуб"}
        resp = self.client.post(reverse("tournaments:create"), data)
        self.assertContains(resp, "Выберите часовой пояс района")
        data["venue_timezone"] = "Asia/Srednekolymsk"
        self.client.post(reverse("tournaments:create"), data)
        self.assertEqual(Tournament.objects.get().timezone, "Asia/Srednekolymsk")

    def test_one_day_ignores_end_and_multiday_validates(self):
        self.client.force_login(self.org)
        base = {"title": "X", "starts_on": self.start.isoformat(), "venue_name": "К", "venue_region": "mos", "venue_settlement": "Н", "organizer_name": "О"}
        resp = self.client.post(reverse("tournaments:create"), {**base, "multiday": "on", "ends_on": self.start.isoformat()})
        self.assertContains(resp, "Дата окончания должна быть позже")
        self.client.post(reverse("tournaments:create"), {**base, "ends_on": (self.start - datetime.timedelta(days=5)).isoformat()})
        t = Tournament.objects.get()
        self.assertEqual(t.ends_on, t.starts_on)

    def test_cannot_jump_ahead(self):
        t = Tournament.objects.create(owner=self.org, title="Черновик", starts_on=self.start, ends_on=self.start)
        self.client.force_login(self.org)
        resp = self.client.get(reverse("tournaments:step", args=[t.pk, 4]))
        self.assertRedirects(resp, reverse("tournaments:step", args=[t.pk, 1]))

    def test_program_and_class_flow_with_price_in_kopecks(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision, "day": (self.start + datetime.timedelta(days=1)).isoformat(), "start_time": "12:30",
            "discipline": "jumping", "name": "Маршрут №2 · 110 см", "program_type": "jumping_jumpoff", "limit": "",
        })
        self.assertEqual(resp.status_code, 302)
        p = ProgramItem.objects.get(name__startswith="Маршрут №2")
        self.assertEqual(p.result_fields, ["penalties", "time", "jumpoff_penalties", "jumpoff_time"])
        t.refresh_from_db()
        resp = self.client.post(reverse("tournaments:class_add", args=[t.pk, p.pk]), {
            "revision": t.revision, "name": "Взрослые", "fee": "3 000,50", "composition": "single", "min_age": "18", "max_age": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(TournamentClass.objects.get(name="Взрослые").fee_kopecks, 300050)

    def test_program_type_must_match_discipline(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision, "day": self.start.isoformat(), "discipline": "dressage", "name": "Езда", "program_type": "jumping_clear",
        })
        self.assertEqual(resp.status_code, 422)
        self.assertContains(resp, "не относится к выбранной дисциплине", status_code=422)

    def test_day_outside_period_rejected(self):
        t = self.make_ready(days=1)
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision, "day": (self.start + datetime.timedelta(days=9)).isoformat(), "discipline": "jumping", "name": "Х", "program_type": "jumping_clear",
        })
        self.assertEqual(resp.status_code, 422)

    def test_negative_and_bad_fee(self):
        t = self.make_ready()
        p = t.programs.first()
        self.client.force_login(self.org)
        for fee in ["-1", "abc", "10,123"]:
            with self.subTest(fee=fee):
                resp = self.client.post(reverse("tournaments:class_add", args=[t.pk, p.pk]), {"revision": t.revision, "name": "Z", "fee": fee, "composition": "single"})
                self.assertEqual(resp.status_code, 422)

    def test_stale_revision_conflict(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision - 1, "day": self.start.isoformat(), "discipline": "jumping", "name": "Х", "program_type": "jumping_clear",
        }, follow=True)
        self.assertContains(resp, "изменились")
        self.assertEqual(t.programs.count(), 1)

    def test_changing_dates_orphans_programs_and_blocks_submit(self):
        t = self.make_ready()
        t.starts_on = t.ends_on = self.start + datetime.timedelta(days=10)
        t.save()
        found = services.problems(t)
        self.assertTrue(any("вне дат" in p.text for p in found))

    def test_regulation_must_be_pdf(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        bad = SimpleUploadedFile("x.pdf", b"<html>not pdf</html>", "application/pdf")
        resp = self.client.post(reverse("tournaments:step", args=[t.pk, 4]), {
            "revision": t.revision, "entry_deadline": t.entry_deadline.isoformat(), "cancel_terms": "", "regulation": bad,
        })
        self.assertContains(resp, "Нужен файл в формате PDF")

    def test_foreign_tournament_closed(self):
        t = self.make_ready()
        self.client.force_login(self.other)
        for url in [
            reverse("tournaments:step", args=[t.pk, 1]),
            reverse("tournaments:step", args=[t.pk, 5]),
            reverse("tournaments:regulation_working", args=[t.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        p = t.programs.first()
        resp = self.client.post(reverse("tournaments:program_delete", args=[t.pk, p.pk]), {"revision": t.revision})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(ProgramItem.objects.filter(pk=p.pk).exists())
        resp = self.client.post(reverse("tournaments:delete", args=[t.pk]), {"revision": t.revision})
        self.assertEqual(resp.status_code, 403)

    def test_delete_only_unpublished_draft(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        t = self.publish(t)
        self.client.post(reverse("tournaments:delete", args=[t.pk]), {"revision": t.revision})
        self.assertTrue(Tournament.objects.filter(pk=t.pk).exists())
        t2 = self.make_ready()
        self.client.post(reverse("tournaments:delete", args=[t2.pk]), {"revision": t2.revision})
        self.assertFalse(Tournament.objects.filter(pk=t2.pk).exists())


class ModerationTests(Base):
    def test_submit_requires_minimum(self):
        t = Tournament.objects.create(owner=self.org, title="Пусто")
        with self.assertRaises(ValueError):
            services.submit(self.org, t.pk, t.revision)

    def test_full_cycle_return_fix_publish(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:step", args=[t.pk, 5]), {"revision": t.revision})
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.REVIEW)
        # во время проверки правка закрыта
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {"revision": t.revision, "day": self.start.isoformat(), "discipline": "jumping", "name": "Х", "program_type": "jumping_clear"})
        self.assertEqual(t.programs.count(), 1)
        # черновик не публичен
        self.client.logout()
        self.assertEqual(self.client.get(reverse("tournaments:public", args=[t.pk])).status_code, 404)
        self.assertNotContains(self.client.get("/"), "Кубок")
        # модератор возвращает
        self.client.force_login(self.mod)
        resp = self.client.post(reverse("moderation:decide", args=[t.pk]), {"revision": t.revision, "decision": "return", "comment": ""}, follow=True)
        self.assertContains(resp, "Напишите, что нужно исправить")
        self.client.post(reverse("moderation:decide", args=[t.pk]), {"revision": t.revision, "decision": "return", "comment": "Положение нечитаемо"})
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.RETURNED)
        self.client.force_login(self.org)
        self.assertContains(self.client.get(reverse("tournaments:step", args=[t.pk, 5])), "Положение нечитаемо")
        # организатор отправляет снова, модератор публикует и подтверждает место
        services.submit(self.org, t.pk, t.revision)
        t.refresh_from_db()
        self.client.force_login(self.mod)
        self.client.post(reverse("moderation:decide", args=[t.pk]), {"revision": t.revision, "decision": "approve", "confirm_venue": "1"})
        t.refresh_from_db()
        self.assertEqual(t.publication, Tournament.Publication.PUBLISHED)
        self.assertEqual(t.status, Tournament.Status.APPROVED)
        self.assertTrue(t.venue.confirmed)
        self.client.logout()
        resp = self.client.get(reverse("tournaments:public", args=[t.pk]))
        self.assertContains(resp, "Кубок «Белая роща»")
        self.assertContains(resp, "2 500 ₽")
        self.assertNotContains(resp, self.org.phone)
        actions = list(AuditLog.objects.filter(target_type="tournament", target_id=t.pk).values_list("action", flat=True))
        for a in ["tournament.submit", "tournament.return", "tournament.approve"]:
            self.assertIn(a, actions)

    def test_only_moderator_decides(self):
        t = self.make_ready()
        services.submit(self.org, t.pk, t.revision)
        t.refresh_from_db()
        self.client.force_login(self.org)
        self.assertEqual(self.client.get(reverse("moderation:queue")).status_code, 403)
        self.assertEqual(self.client.post(reverse("moderation:decide", args=[t.pk]), {"revision": t.revision, "decision": "approve"}).status_code, 403)
        t.refresh_from_db()
        self.assertEqual(t.publication, Tournament.Publication.NONE)

    def test_moderator_cannot_approve_stale_or_twice(self):
        t = self.make_ready()
        services.submit(self.org, t.pk, t.revision)
        t.refresh_from_db()
        with self.assertRaises(services.Conflict):
            services.approve(self.mod, t.pk, t.revision - 1)
        services.approve(self.mod, t.pk, t.revision)
        with self.assertRaises(services.Conflict):
            services.approve(self.mod, t.pk, t.revision)

    def test_unchanged_published_cannot_be_resubmitted(self):
        t = self.publish(self.make_ready())
        with self.assertRaises(services.Conflict):
            services.submit(self.org, t.pk, t.revision)

    def test_edit_after_publish_keeps_public_version(self):
        t = self.publish(self.make_ready())
        c = TournamentClass.objects.get()
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:class_save", args=[t.pk, c.program_id, c.pk]), {"revision": t.revision, "name": "Любители", "fee": "9999", "composition": "single"})
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.DRAFT)
        self.assertEqual(t.publication, Tournament.Publication.PUBLISHED)
        resp = self.client.get(reverse("tournaments:public", args=[t.pk]))
        self.assertContains(resp, "2 500 ₽")
        self.assertNotContains(resp, "9 999")
        services.submit(self.org, t.pk, t.revision)
        t.refresh_from_db()
        services.approve(self.mod, t.pk, t.revision)
        self.assertContains(self.client.get(reverse("tournaments:public", args=[t.pk])), "9 999 ₽")

    def test_new_regulation_does_not_replace_published_until_approved(self):
        t = self.publish(self.make_ready())
        old = t.published_regulation
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:step", args=[t.pk, 4]), {
            "revision": t.revision, "entry_deadline": t.entry_deadline.isoformat(), "cancel_terms": "x",
            "regulation": SimpleUploadedFile("new.pdf", PDF + b"new", "application/pdf"),
        })
        t.refresh_from_db()
        self.assertNotEqual(t.regulation.name, old)
        self.assertEqual(t.published_regulation, old)
        resp = self.client.get(reverse("tournaments:public_regulation", args=[t.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"new", b"".join(resp.streaming_content))

    def test_cancel_published(self):
        t = self.publish(self.make_ready())
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:cancel", args=[t.pk]), {"revision": t.revision, "reason": ""})
        t.refresh_from_db()
        self.assertEqual(t.publication, Tournament.Publication.PUBLISHED)
        self.client.post(reverse("tournaments:cancel", args=[t.pk]), {"revision": t.revision, "reason": "Непогода"})
        t.refresh_from_db()
        self.assertEqual(t.publication, Tournament.Publication.CANCELLED)
        self.client.logout()
        self.assertContains(self.client.get(reverse("tournaments:public", args=[t.pk])), "Непогода")


class CalendarTests(Base):
    def test_filters_and_open_state(self):
        a = self.publish(self.make_ready())
        b = self.make_ready()
        b.title = "Выездка «Осень»"
        b.save()
        p = b.programs.first()
        p.discipline, p.program_type = "dressage", "dressage_test"
        p.save()
        b.venue.region = "sve"
        b.venue.save()
        b = self.publish(b)
        resp = self.client.get("/")
        self.assertContains(resp, a.pub_title)
        self.assertContains(resp, "Приём заявок открыт")
        resp = self.client.get("/?discipline=dressage")
        self.assertContains(resp, "Выездка «Осень»")
        self.assertNotContains(resp, "Кубок «Белая роща»")
        resp = self.client.get("/?region=sve&q=осень")
        self.assertContains(resp, "Выездка «Осень»")
        self.assertContains(resp, "Найдено: 1")
        resp = self.client.get("/?q=несуществующее")
        self.assertContains(resp, "По этим условиям ничего не найдено")

    def test_closed_after_deadline_in_venue_timezone(self):
        t = self.publish(self.make_ready())
        t.pub_deadline = timezone.localdate() - datetime.timedelta(days=1)
        t.save()
        self.assertFalse(t.public_entries_open())
        resp = self.client.get("/?open=1")
        self.assertNotContains(resp, t.pub_title)


class TeamTests(Base):
    def test_invite_accept_by_same_phone_and_revoke(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:team", args=[t.pk]), {"phone": "8 999 000 01 02", "role": "organizer"})
        m = TeamMember.objects.get()
        self.assertEqual(m.phone, self.other.phone)
        # до принятия прав нет
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("tournaments:step", args=[t.pk, 1])).status_code, 403)
        # чужой номер принять не может
        self.client.force_login(self.coach)
        self.assertEqual(self.client.post(reverse("tournaments:invitation_respond", args=[m.pk]), {"decision": "accept"}).status_code, 403)
        # приглашённый принимает
        self.client.force_login(self.other)
        self.client.post(reverse("tournaments:invitation_respond", args=[m.pk]), {"decision": "accept"})
        self.assertEqual(self.client.get(reverse("tournaments:step", args=[t.pk, 1])).status_code, 200)
        self.assertContains(self.client.get(reverse("tournaments:mine")), t.title)
        # отзыв закрывает доступ сразу
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:team_revoke", args=[t.pk, m.pk]))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("tournaments:step", args=[t.pk, 1])).status_code, 403)

    def test_judge_role_cannot_edit_or_invite(self):
        t = self.make_ready()
        TeamMember.objects.create(tournament=t, phone=self.other.phone, user=self.other, role="judge", state="accepted", invited_by=self.org)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("tournaments:team", args=[t.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("tournaments:team", args=[t.pk]), {"phone": "+79990000109", "role": "judge"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("tournaments:step", args=[t.pk, 1])).status_code, 403)

    def test_duplicate_invite_rejected(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:team", args=[t.pk]), {"phone": "+79990000102", "role": "judge"})
        resp = self.client.post(reverse("tournaments:team", args=[t.pk]), {"phone": "+79990000102", "role": "judge"})
        self.assertContains(resp, "уже выдана")
        self.assertEqual(TeamMember.objects.count(), 1)

    def test_stranger_cannot_see_team(self):
        t = self.make_ready()
        self.client.force_login(self.coach)
        self.assertEqual(self.client.get(reverse("tournaments:team", args=[t.pk])).status_code, 403)
