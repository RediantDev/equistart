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
from apps.sport.models import ClassTemplate
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
            venue=venue, timezone="Europe/Moscow", contact_name="КСК «Белая роща»", contact="+7 900 000-00-00",
            entry_deadline=self.start - datetime.timedelta(days=3), cancel_terms="Возврат до срока подачи.",
        )
        t.regulation.save("p.pdf", SimpleUploadedFile("p.pdf", PDF, "application/pdf"))
        p = ProgramItem.objects.create(tournament=t, day=self.start, discipline="jumping", name="Маршрут №1 · 80 см", program_type="clear_speed", table="A", heights=[80])
        TournamentClass.objects.create(program=p, name="Любители")
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
            "contact_name": "КСК «Озёрный»", "contact": "+7 900 000-00-00",
        })
        t = Tournament.objects.get()
        self.assertRedirects(resp, reverse("tournaments:step", args=[t.pk, 2]))
        self.assertEqual(t.timezone, "Asia/Yekaterinburg")
        self.assertEqual(t.ends_on, self.start + datetime.timedelta(days=1))
        self.assertEqual(t.status, Tournament.Status.DRAFT)
        self.assertFalse(t.venue.confirmed)

    def test_yakutia_uses_main_zone(self):
        # тест 4: поле «Часовой пояс района» убрано — берётся основной пояс региона
        self.client.force_login(self.org)
        data = {"title": "Якутия", "starts_on": self.start.isoformat(), "venue_name": "Клуб", "venue_region": "sa",
                "venue_settlement": "Якутск", "contact_name": "Клуб", "contact": "+79001112233"}
        self.client.post(reverse("tournaments:create"), data)
        self.assertEqual(Tournament.objects.get().timezone, "Asia/Yakutsk")

    def test_one_day_ignores_end_and_multiday_validates(self):
        self.client.force_login(self.org)
        base = {"title": "X", "starts_on": self.start.isoformat(), "venue_name": "К", "venue_region": "mos", "venue_settlement": "Н", "contact_name": "О", "contact": "+79001112233"}
        resp = self.client.post(reverse("tournaments:create"), {**base, "multiday": "on", "ends_on": self.start.isoformat()})
        self.assertContains(resp, "Дата окончания должна быть позже")
        self.client.post(reverse("tournaments:create"), {**base, "ends_on": (self.start - datetime.timedelta(days=5)).isoformat()})
        t = Tournament.objects.get()
        self.assertEqual(t.ends_on, t.starts_on)

    def test_cannot_jump_ahead(self):
        t = Tournament.objects.create(owner=self.org, title="Черновик", starts_on=self.start, ends_on=self.start)
        self.client.force_login(self.org)
        resp = self.client.get(reverse("tournaments:step", args=[t.pk, 3]))
        self.assertRedirects(resp, reverse("tournaments:step", args=[t.pk, 1]))
        self.assertEqual(self.client.get(reverse("tournaments:step", args=[t.pk, 4])).status_code, 200)  # проверка доступна всегда

    def test_program_and_class_flow(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision, "day": (self.start + datetime.timedelta(days=1)).isoformat(), "start_time": "12:30",
            "discipline": "jumping", "name": "Маршрут №2 · 110 см", "program_type": "jumpoff", "table": "A", "heights_text": "100, 110",
            "templates": [str(ClassTemplate.objects.get(name="Юноши").pk)],
        })
        self.assertEqual(resp.status_code, 302)
        p = ProgramItem.objects.get(name__startswith="Маршрут №2")
        self.assertEqual((p.program_type, p.heights, p.table), ("jumpoff", [100, 110], "A"))
        self.assertEqual(p.article_label, "гл. X, п. 16.4.2.2, табл. «A» (ст. 238.2.2 FEI)")
        t.refresh_from_db()
        resp = self.client.post(reverse("tournaments:class_add", args=[t.pk, p.pk]), {
            "revision": t.revision, "name": "Взрослые", "height": "110", "birth_year_to": str(self.start.year - 18),
        })
        self.assertEqual(resp.status_code, 302)
        c = TournamentClass.objects.get(name="Взрослые")
        self.assertEqual((c.birth_year_to, c.height, c.label), (self.start.year - 18, 110, "Взрослые · 110 см"))
        # шаблон зачёта: возраст пересчитывается в годы рождения от года турнира
        tpl = ClassTemplate.objects.get(name="Дети")
        t.refresh_from_db()
        resp = self.client.post(reverse("tournaments:class_from_template", args=[t.pk, p.pk]), {"revision": t.revision, "template": tpl.pk, "height": "100"})
        self.assertEqual(resp.status_code, 302)
        c = TournamentClass.objects.get(program=p, name="Дети")
        self.assertEqual((c.birth_year_from, c.birth_year_to, c.height), (self.start.year - 14, self.start.year - 12, 100))

    def test_program_type_must_match_discipline(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision, "day": self.start.isoformat(), "discipline": "dressage", "name": "Езда", "program_type": "no_time",
        })
        self.assertEqual(resp.status_code, 422)
        self.assertContains(resp, "Выберите езду", status_code=422)
        t.level = "regional"
        t.save()
        t.refresh_from_db()
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision, "day": self.start.isoformat(), "discipline": "jumping", "name": "М", "program_type": "two_phases_special",
            "table": "B2", "heights_text": "130",
        })
        self.assertEqual(resp.status_code, 422)  # B2 на официальных — только до 130 см (гл. X п. 16.3)

    def test_day_outside_period_rejected(self):
        t = self.make_ready(days=1)
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision, "day": (self.start + datetime.timedelta(days=9)).isoformat(), "discipline": "jumping", "name": "Х", "program_type": "no_time", "table": "A", "heights_text": "80",
            "templates": [str(ClassTemplate.objects.get(name="Юноши").pk)],
        })
        self.assertEqual(resp.status_code, 422)

    def test_stale_revision_conflict(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {
            "revision": t.revision - 1, "day": self.start.isoformat(), "discipline": "jumping", "name": "Х", "program_type": "no_time", "table": "A", "heights_text": "80",
            "templates": [str(ClassTemplate.objects.get(name="Юноши").pk)],
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
        resp = self.client.post(reverse("tournaments:step", args=[t.pk, 3]), {
            "revision": t.revision, "entry_deadline": t.entry_deadline.isoformat(), "cancel_terms": "", "regulation": bad,
        })
        self.assertContains(resp, "Нужен файл в формате PDF")

    def test_foreign_tournament_closed(self):
        t = self.make_ready()
        self.client.force_login(self.other)
        for url in [
            reverse("tournaments:step", args=[t.pk, 1]),
            reverse("tournaments:step", args=[t.pk, 4]),
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
        self.client.post(reverse("tournaments:step", args=[t.pk, 4]), {"revision": t.revision})
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.REVIEW)
        # во время проверки правка закрыта
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), {"revision": t.revision, "day": self.start.isoformat(), "discipline": "jumping", "name": "Х", "program_type": "no_time", "table": "A", "heights_text": "80", "templates": [str(ClassTemplate.objects.get(name="Юноши").pk)]})
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
        self.assertContains(self.client.get(reverse("tournaments:step", args=[t.pk, 4])), "Положение нечитаемо")
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
        self.assertContains(resp, "Любители")
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
        self.client.post(reverse("tournaments:class_save", args=[t.pk, c.program_id, c.pk]), {"revision": t.revision, "name": "Любители 2"})
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.DRAFT)
        self.assertEqual(t.publication, Tournament.Publication.PUBLISHED)
        self.client.get(reverse("tournaments:public", args=[t.pk]))  # сообщение «Зачёт сохранён»
        resp = self.client.get(reverse("tournaments:public", args=[t.pk]))
        self.assertContains(resp, "Любители")
        self.assertNotContains(resp, "Любители 2")
        services.submit(self.org, t.pk, t.revision)
        t.refresh_from_db()
        services.approve(self.mod, t.pk, t.revision)
        self.assertContains(self.client.get(reverse("tournaments:public", args=[t.pk])), "Любители 2")

    def test_new_regulation_does_not_replace_published_until_approved(self):
        t = self.publish(self.make_ready())
        old = t.published_regulation
        self.client.force_login(self.org)
        self.client.post(reverse("tournaments:step", args=[t.pk, 3]), {
            "revision": t.revision, "entry_deadline": t.entry_deadline.isoformat(), "entry_deadline_time": "18:00", "cancel_terms": "x",
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
        from apps.sport.models import DressageTest

        test = DressageTest.objects.filter(is_official=True).first()
        p.discipline, p.program_type, p.dressage_test, p.test_snapshot, p.heights = "dressage", "dressage_test", test, test.snapshot(), []
        p.save()
        b.venue.region = "sve"
        b.venue.save()
        b = self.publish(b)
        resp = self.client.get("/")
        self.assertContains(resp, a.pub_title)
        self.assertContains(resp, "tcard__st--info")  # тест 5.2, карточка A: «Приём до …» синей точкой
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


class ContactPhoneVisibilityTests(Base):
    """Решение 26.10.2026: телефон контакта турнира виден только вошедшим (ISSUES-run1 №32)."""

    def test_guest_sees_login_hint_instead_of_phone(self):
        t = self.publish(self.make_ready())
        resp = self.client.get(reverse("tournaments:public", args=[t.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "000-00-00")
        self.assertNotContains(resp, "tel:")
        self.assertContains(resp, "Войдите, чтобы увидеть телефон")

    def test_logged_in_user_sees_phone(self):
        t = self.publish(self.make_ready())
        self.client.force_login(self.coach)
        resp = self.client.get(reverse("tournaments:public", args=[t.pk]))
        self.assertContains(resp, "000-00-00")
        self.assertNotContains(resp, "Войдите, чтобы увидеть телефон")
