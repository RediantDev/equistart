import datetime
import io
import zipfile

from django.test import override_settings
from django.urls import reverse

from apps.conduct import services
from apps.conduct.models import Result, ResultPublication, StartListPublication, StartSlot
from apps.entries import services as eservices
from apps.entries.models import Entry
from apps.entries.tests.test_entries import TMP, Base
from apps.profiles.models import Horse
from apps.tournaments.models import ProgramItem, Tournament, TournamentClass


@override_settings(MEDIA_ROOT=TMP)
class ConductBase(Base):
    def setUp(self):
        super().setUp()
        self.p1.start_time = datetime.time(10, 0)
        self.p1.slot_minutes = 3
        self.p1.save()
        self.h3 = Horse.objects.create(name="Гранит", birth_year=2012, color="grey", sex="gelding", breed="орловская", manager=self.coach)
        app = self.submit(self.coach, [
            eservices.Line(self.a1, self.h1, self.c_kids),
            eservices.Line(self.a2, self.h3, self.c_open),
        ])
        self.e1, self.e2 = app.entries.order_by("pk")
        self.accept(self.e1, self.e2)

    def accept(self, *entries):
        items = []
        for e in entries:
            e.refresh_from_db()
            items.append((e.pk, e.revision))
        eservices.decide(self.judge, self.t.pk, items, "accept")


class DrawTests(ConductBase):
    def test_draw_takes_only_accepted(self):
        app = self.submit(self.coach, [eservices.Line(self.a2, self.h1, self.c_open)], key="k2")
        pending = app.entries.get()
        n = services.draw(self.judge, self.p1.pk)
        self.assertEqual(n, 2)
        ids = set(self.p1.slots.values_list("entry_id", flat=True))
        self.assertEqual(ids, {self.e1.pk, self.e2.pk})
        self.assertNotIn(pending.pk, ids)
        times = sorted(self.p1.slots.values_list("start_time", flat=True))
        self.assertEqual(times, [datetime.time(10, 0), datetime.time(10, 3)])

    def test_redraw_keeps_started(self):
        services.draw(self.judge, self.p1.pk)
        first = self.p1.slots.get(position=1)
        services.set_status(self.judge, first.pk, StartSlot.Status.STARTED)
        for _ in range(5):
            services.draw(self.judge, self.p1.pk)
            self.assertEqual(self.p1.slots.get(pk=first.pk).position, 1)

    def test_move_and_locked(self):
        services.draw(self.judge, self.p1.pk)
        a, b = self.p1.slots.order_by("position")
        services.move(self.judge, b.pk, 1)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((b.position, a.position), (1, 2))
        self.assertEqual(b.start_time, datetime.time(10, 0))
        services.set_status(self.judge, b.pk, StartSlot.Status.STARTED)
        with self.assertRaises(ValueError):
            services.move(self.judge, a.pk, 1)
        with self.assertRaises(ValueError):
            services.move(self.judge, b.pk, 2)

    def test_status_transitions(self):
        services.draw(self.judge, self.p1.pk)
        s = self.p1.slots.first()
        services.set_status(self.judge, s.pk, StartSlot.Status.NO_SHOW)
        self.assertEqual(Result.objects.get(entry=s.entry).status, Result.Status.NO_SHOW)
        with self.assertRaises(ValueError):
            services.set_status(self.judge, s.pk, StartSlot.Status.STARTED)

    def test_conflicts_by_horse_interval(self):
        p2 = ProgramItem.objects.create(tournament=self.t, day=self.start, start_time=datetime.time(10, 5), discipline="jumping", name="Маршрут №2", program_type="jumping_clear", result_fields=["penalties"])
        c2 = TournamentClass.objects.create(program=p2, name="Открытый-2")
        app = self.submit(self.coach, [eservices.Line(self.a2, self.h1, c2)], key="k3")
        self.accept(app.entries.get())
        services.draw(self.judge, self.p1.pk)
        services.draw(self.judge, p2.pk)
        self.assertEqual(services.conflicts(self.t), {})
        self.t.min_rest_minutes = 30
        self.t.save()
        conf = services.conflicts(self.t)
        self.assertTrue(any("лошадь Барон" in x for v in conf.values() for x in v))

    def test_publish_versions_and_public_tab(self):
        services.draw(self.judge, self.p1.pk)
        services.publish_start_list(self.judge, self.p1.pk)
        services.publish_start_list(self.judge, self.p1.pk)
        self.assertEqual(list(self.p1.start_publications.values_list("version", flat=True)), [2, 1])
        # отмена после публикации — в публичной таблице отметка «Снят», без телефонов
        self.e1.refresh_from_db()
        eservices.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision)], "cancel", "заболела лошадь")
        resp = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=starts")
        self.assertContains(resp, "Смирнова Лиза")
        self.assertContains(resp, "Снят")
        self.assertNotContains(resp, self.coach.phone)
        self.assertNotContains(resp, "заболела")
        self.assertNotContains(resp, self.a1.birth_date.strftime("%d.%m.%Y"))

    def test_unpublished_start_list_hidden(self):
        services.draw(self.judge, self.p1.pk)
        resp = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=starts")
        self.assertContains(resp, "ещё не опубликованы")
        self.assertEqual(self.client.get(reverse("conduct:startlist_xlsx", args=[self.t.pk, self.p1.pk])).status_code, 404)


class ResultTests(ConductBase):
    def test_save_and_publish_results(self):
        services.draw(self.judge, self.p1.pk)
        services.save_result(self.judge, self.e1.pk, {"penalties": "4"}, "2", "")
        services.save_result(self.judge, self.e2.pk, {"penalties": "0,0"}, "1", "")
        self.assertEqual(StartSlot.objects.get(entry=self.e1).status, StartSlot.Status.FINISHED)
        with self.assertRaises(ValueError):
            services.save_result(self.judge, self.e1.pk, {"penalties": "abc"}, "", "")
        resp = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=results")
        self.assertContains(resp, "Результаты ещё не опубликованы")
        services.publish_results(self.judge, self.c_kids.pk)
        pub = ResultPublication.objects.get(tclass=self.c_kids)
        self.assertEqual(pub.rows[0]["values"], {"penalties": 4.0})
        services.save_result(self.judge, self.e1.pk, {"penalties": "8"}, "2", "")
        resp = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=results")
        self.assertContains(resp, '<td class="num">4</td>', html=False)  # опубликованная версия, не рабочая
        services.publish_results(self.judge, self.c_kids.pk)
        self.assertEqual(self.c_kids.result_publications.count(), 2)
        resp = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=results")
        self.assertContains(resp, '<td class="num">8</td>')
        x = self.client.get(reverse("conduct:results_xlsx", args=[self.t.pk, self.c_kids.pk]))
        self.assertEqual(x.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(x.content)) as z:
            self.assertIn("Смирнова", z.read("xl/worksheets/sheet1.xml").decode())

    def test_live_save_starts_next(self):
        services.draw(self.judge, self.p1.pk)
        first, second = self.p1.slots.order_by("position")
        self.client.force_login(self.judge)
        r = self.client.get(reverse("conduct:live", args=[self.t.pk, self.p1.pk]))
        self.assertContains(r, first.entry.athlete_snapshot["name"])
        self.client.post(reverse("conduct:live_save", args=[self.t.pk, self.p1.pk]), {"slot": first.pk, "v_penalties": "0", "status": "", "next_start": "1"})
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.status, StartSlot.Status.FINISHED)
        self.assertEqual(second.status, StartSlot.Status.STARTED)

    def test_finish_tournament(self):
        self.client.force_login(self.org)
        r = self.client.get(reverse("conduct:overview", args=[self.t.pk]))
        self.assertContains(r, "Не опубликованы результаты")
        self.client.post(reverse("conduct:finish", args=[self.t.pk]))
        self.t.refresh_from_db()
        self.assertEqual(self.t.publication, Tournament.Publication.PUBLISHED)
        self.client.post(reverse("conduct:finish", args=[self.t.pk]), {"confirm": "1"})
        self.t.refresh_from_db()
        self.assertEqual(self.t.publication, Tournament.Publication.FINISHED)
        self.assertEqual(self.client.get(reverse("tournaments:public", args=[self.t.pk])).status_code, 200)


class PermissionTests(ConductBase):
    def test_guest_and_stranger(self):
        services.draw(self.judge, self.p1.pk)
        slot = self.p1.slots.first()
        urls_get = [reverse("conduct:overview", args=[self.t.pk]), reverse("conduct:program", args=[self.t.pk, self.p1.pk]), reverse("conduct:live", args=[self.t.pk, self.p1.pk])]
        posts = [
            (reverse("conduct:action", args=[self.t.pk, self.p1.pk, "draw"]), {}),
            (reverse("conduct:action", args=[self.t.pk, self.p1.pk, "publish"]), {}),
            (reverse("conduct:action", args=[self.t.pk, self.p1.pk, "result"]), {"entry": slot.entry_id, "v_penalties": "0"}),
            (reverse("conduct:live_save", args=[self.t.pk, self.p1.pk]), {"slot": slot.pk}),
            (reverse("conduct:settings", args=[self.t.pk]), {"min_rest_minutes": "5"}),
            (reverse("conduct:finish", args=[self.t.pk]), {"confirm": "1"}),
        ]
        for u in urls_get:
            self.assertEqual(self.client.get(u).status_code, 302, u)
        for u, d in posts:
            self.assertEqual(self.client.post(u, d).status_code, 302, u)
        self.client.force_login(self.other)
        for u in urls_get:
            self.assertIn(self.client.get(u).status_code, (403, 404), u)
        for u, d in posts:
            self.assertIn(self.client.post(u, d).status_code, (403, 404), u)
        self.assertFalse(StartListPublication.objects.exists())
        self.assertFalse(Result.objects.exists())
        self.t.refresh_from_db()
        self.assertEqual(self.t.min_rest_minutes, 0)
        self.assertEqual(self.t.publication, Tournament.Publication.PUBLISHED)

    def test_program_of_other_tournament(self):
        other_t = self.make_tournament()
        p = other_t.programs.first()
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(reverse("conduct:program", args=[self.t.pk, p.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("conduct:program", args=[other_t.pk, p.pk])).status_code, 403)

    def test_judge_cannot_finish(self):
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(reverse("conduct:overview", args=[self.t.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("conduct:finish", args=[self.t.pk]), {"confirm": "1"}).status_code, 404)

    def test_pages_render(self):
        services.draw(self.judge, self.p1.pk)
        self.client.force_login(self.judge)
        for u in [reverse("conduct:overview", args=[self.t.pk]), reverse("conduct:program", args=[self.t.pk, self.p1.pk]), reverse("conduct:live", args=[self.t.pk, self.p1.pk])]:
            self.assertEqual(self.client.get(u).status_code, 200, u)
