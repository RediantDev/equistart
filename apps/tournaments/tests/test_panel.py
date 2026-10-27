"""Пульт администратора (решение 26.10.2026): доступ, очередь, состояние сервиса, цифры."""

import datetime
import io

from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import Club
from apps.core.errors import server_error
from apps.core.models import AuditLog
from apps.tournaments import services

from .test_tournaments import Base, user


class PanelTests(Base):
    url = reverse("moderation:panel")

    def test_guest_and_non_moderator(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])
        for u in (self.org, self.coach):
            self.client.force_login(u)
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_empty_panel(self):
        self.client.force_login(self.mod)
        resp = self.client.get(self.url)
        self.assertContains(resp, "Всё проверено")
        self.assertContains(resp, "Копии не настроены")
        self.assertContains(resp, "Работает")

    def test_waiting_tournament_and_club_with_overdue(self):
        t = self.make_ready()
        services.submit(self.org, t.pk, t.revision)
        manager = user("+79990000105", is_club=True)
        club = Club.objects.create(manager=manager, name="КСК «Пегас»", region="mos", settlement="Москва", address="ул. Конная, 1",
                                   status=Club.Status.REVIEW, submitted_at=timezone.now() - datetime.timedelta(days=2))
        self.client.force_login(self.mod)
        resp = self.client.get(self.url)
        self.assertContains(resp, "Кубок «Белая роща»")
        self.assertContains(resp, reverse("moderation:detail", args=[t.pk]))
        self.assertContains(resp, "КСК «Пегас»")
        self.assertContains(resp, reverse("moderation:club", args=[club.pk]))
        self.assertEqual(resp.context["waiting_total"], 2)
        self.assertEqual(resp.context["overdue_total"], 1)  # турнир отправлен только что, клуб — двое суток назад
        # телефоны пользователей на пульте не показываются
        self.assertNotContains(resp, "+79990000105")

    def test_backup_and_errors(self):
        call_command("backup_done", "20261026-0315", stdout=io.StringIO())
        server_error(RequestFactory().get("/broken/"))
        self.client.force_login(self.mod)
        resp = self.client.get(self.url)
        self.assertTrue(resp.context["backup_ok"])
        self.assertNotContains(resp, "Копии не настроены")
        self.assertEqual(resp.context["errors_day"], 1)
        # старая копия — тревога
        AuditLog.objects.filter(action="service.backup").update(created_at=timezone.now() - datetime.timedelta(days=2))
        resp = self.client.get(self.url)
        self.assertFalse(resp.context["backup_ok"])
        self.assertContains(resp, "Старше суток")

    def test_stats(self):
        self.publish(self.make_ready())
        self.client.force_login(self.mod)
        stats = self.client.get(self.url).context["stats"]
        self.assertEqual(stats["tournaments_live"], 1)
        self.assertEqual(stats["tournaments_week"], 1)
        self.assertEqual(stats["users"], 4)

    def test_nav_link_for_moderator_only(self):
        self.client.force_login(self.mod)
        self.assertContains(self.client.get("/"), self.url)
        self.client.force_login(self.org)
        self.assertNotContains(self.client.get("/"), self.url)
