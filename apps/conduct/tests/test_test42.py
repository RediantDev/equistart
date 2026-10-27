"""Тест 4.2 (23.10.2026): панель турнира, отклонение модератором, время окончания приёма, ручная бронь,
несколько высот в одном маршруте, постановка в порядок без денника, выгрузки только для команды."""

import datetime

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.conduct import services
from apps.conduct.models import StartSlot
from apps.entries.tests.test_entries import TMP
from apps.stables.models import Booking, StableCategory
from apps.tournaments import services as tservices
from apps.tournaments.models import Tournament

from .test_conduct import ConductBase


@override_settings(MEDIA_ROOT=TMP)
class Test42Tests(ConductBase):
    def test_hub_for_team_only(self):
        url = reverse("tournaments:hub", args=[self.t.pk])
        self.client.force_login(self.judge)
        r = self.client.get(url)
        self.assertContains(r, "Заявки")
        self.assertContains(r, "Проведение")
        self.client.force_login(self.coach)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_public_page_has_single_staff_button(self):
        self.client.force_login(self.judge)
        r = self.client.get(reverse("tournaments:public", args=[self.t.pk]))
        self.assertContains(r, "Панель турнира")
        self.assertNotContains(r, "Конюшня и услуги")

    def test_deadline_time(self):
        t = self.t
        tz = __import__("zoneinfo").ZoneInfo(t.pub_timezone)
        now = timezone.now().astimezone(tz)
        t.pub_deadline, t.pub_deadline_time = now.date(), (now - datetime.timedelta(minutes=5)).time().replace(second=0, microsecond=0)
        if t.pub_deadline_time > now.time():  # около полуночи
            return
        self.assertFalse(t.public_entries_open())
        t.pub_deadline_time = datetime.time(23, 59)
        self.assertTrue(t.public_entries_open() or now.time() > datetime.time(23, 59))

    def test_force_add_blocked_pair(self):
        cat = StableCategory.objects.create(tournament=self.t, name="К", available_from=self.t.starts_on, available_to=self.t.ends_on, capacity=5)
        Booking.objects.create(tournament=self.t, category=cat, horse=self.e1.horse, horse_name="x", requested_by=self.coach,
                               arrive=self.t.starts_on, depart=self.t.ends_on, required=True, idempotency_key="b1")
        services.draw(self.judge, self.p1.pk)
        self.assertFalse(StartSlot.objects.filter(entry=self.e1).exists())
        services.force_add(self.judge, self.p1.pk, self.e1.pk)
        self.assertTrue(StartSlot.objects.filter(entry=self.e1).exists())

    def test_staff_can_book_for_participant(self):
        from apps.stables import services as ss

        cat = StableCategory.objects.create(tournament=self.t, name="К", available_from=self.t.starts_on,
                                            available_to=self.t.starts_on + datetime.timedelta(days=2), capacity=5)
        b = ss.book(self.org, cat.pk, self.e1.horse, cat.available_from, cat.available_from + datetime.timedelta(days=1), "staff1")
        self.assertEqual(b.requested_by, self.org)

    def test_xlsx_only_for_team(self):
        services.draw(self.judge, self.p1.pk)
        services.publish_start_list(self.judge, self.p1.pk)
        url = reverse("conduct:startlist_xlsx", args=[self.t.pk, self.p1.pk])
        self.client.force_login(self.coach)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(url).status_code, 200)


class Test42ModerationTests(ConductBase):
    def test_reject_unpublished(self):
        t = Tournament.objects.create(owner=self.org, title="Ненастоящий", starts_on=self.t.starts_on, ends_on=self.t.starts_on)
        t.status = Tournament.Status.REVIEW
        t.save()
        tservices.reject(self.mod, t.pk, t.revision, "дубль")
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.REJECTED)
        self.assertTrue(t.is_locked)
        with self.assertRaises(Exception):
            tservices.submit(self.org, t.pk, t.revision)


@override_settings(MEDIA_ROOT=TMP)
class Test42ApplyTests(ConductBase):
    def test_two_heights_same_route(self):
        from apps.entries.models import ApplicationDraft

        self.p1.heights = [60, 70]
        self.p1.save()
        self.client.force_login(self.coach)
        url = reverse("entries:apply", args=[self.t.pk])
        self.client.post(url, {"action": "pair_add", "athlete": self.a2.pk, "horse": self.h1.pk})
        r = self.client.post(url, {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk,
                                   f"sel_{self.p1.pk}": [f"{self.c_open.pk}|60", f"{self.c_open.pk}|70"]})
        self.assertEqual(r.status_code, 302)
        self.assertIn("open=-", r["Location"])
        lines = ApplicationDraft.objects.get(user=self.coach, tournament=self.t).data["lines"]
        self.assertEqual(sorted(l["ht"] for l in lines if l["c"] == self.c_open.pk), [60, 70])

    def test_single_height_allows_one_class(self):
        self.client.force_login(self.coach)
        url = reverse("entries:apply", args=[self.t.pk])
        self.client.post(url, {"action": "pair_add", "athlete": self.a2.pk, "horse": self.h1.pk})
        r = self.client.post(url, {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk,
                                   f"sel_{self.p1.pk}": [str(self.c_open.pk), str(self.c_kids.pk)]})
        self.assertContains(r, "в одном маршруте пара выступает в одном зачёте")
