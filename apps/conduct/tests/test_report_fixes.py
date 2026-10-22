"""Исправления по отчёту тестирования 22.10.2026 (К1–К5)."""

import datetime

from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from apps.conduct import scoring, services
from apps.conduct.models import Result
from apps.conduct.tests.test_conduct import ConductBase
from apps.conduct.tests.test_scoring import rnd
from apps.entries import services as eservices
from apps.entries.models import Entry
from apps.entries.tests.test_entries import TMP
from apps.profiles.models import Horse


@override_settings(MEDIA_ROOT=TMP)
class ReportFixesTests(ConductBase):
    def jumping(self, heights=(80,)):
        self.p1.program_type, self.p1.table, self.p1.result_fields, self.p1.heights = "clear_speed", "A", [], list(heights)
        self.p1.save()

    def test_k1_no_form_field_limit(self):
        self.assertIsNone(settings.DATA_UPLOAD_MAX_NUMBER_FIELDS)
        self.client.force_login(self.judge)
        data = {"action": "accept", "confirm": "1", "entry": [str(self.e1.pk)], f"rev-{self.e1.pk}": "1"}
        for i in range(3000):  # как 1500 строк заявок
            data[f"rev-x{i}"] = "1"
        resp = self.client.post(reverse("entries:decide", args=[self.t.pk]), data)
        self.assertNotEqual(resp.status_code, 400)

    def test_k2_results_grouped_by_height_inside_class(self):
        self.jumping((60, 70))
        Entry.objects.filter(pk=self.e1.pk).update(height=60)
        Entry.objects.filter(pk=self.e2.pk).update(height=70, tclass=self.c_kids)
        services.draw(self.judge, self.p1.pk)
        scoring.save_course(self.judge, self.p1, {"main_length": "350", "main_speed": "350"})
        scoring.save_round(self.judge, self.e1.pk, "main", rnd(0, 0, "55.10"))
        scoring.save_round(self.judge, self.e2.pk, "main", rnd(1, 0, "50.00"))
        pub = services.publish_results(self.judge, self.c_kids.pk)
        self.assertEqual([(r["height"], r["place"]) for r in pub.rows], [(60, 1), (70, 1)])
        page = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=results").content.decode()
        self.assertIn("Высота 60 см", page)
        self.assertIn("Высота 70 см", page)
        self.assertLess(page.index("Высота 60 см"), page.index("Высота 70 см"))
        self.assertIn("55,10", page)  # время с точностью хронометража

    def test_k3_no_start_times_in_jumping_optional_in_dressage(self):
        services.draw(self.judge, self.p1.pk)
        self.assertEqual(set(self.p1.slots.values_list("start_time", flat=True)), {None})
        pub = services.publish_start_list(self.judge, self.p1.pk)
        page = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=starts").content.decode()
        self.assertNotIn("<th class=\"num\">Время</th>", page)
        self.client.force_login(self.judge)
        self.client.post(reverse("conduct:action", args=[self.t.pk, self.p1.pk, "timing"]), {"slot_minutes": "5", "start_times": "1"})
        self.assertEqual(set(self.p1.slots.values_list("start_time", flat=True)), {None})
        # выездка: включено — время считается; выключено — нет
        self.p1.discipline, self.p1.start_times = "dressage", True
        self.p1.save()
        services._recompute_times(self.p1)
        self.assertEqual(sorted(self.p1.slots.values_list("start_time", flat=True)), [datetime.time(10, 0), datetime.time(10, 3)])
        self.client.post(reverse("conduct:action", args=[self.t.pk, self.p1.pk, "timing"]), {"slot_minutes": "5"})
        self.assertEqual(set(self.p1.slots.values_list("start_time", flat=True)), {None})

    def test_k4_retired_is_labelled_and_reason_only_for_elimination(self):
        self.jumping()
        services.draw(self.judge, self.p1.pk)
        scoring.save_course(self.judge, self.p1, {"main_length": "350", "main_speed": "350"})
        scoring.save_round(self.judge, self.e1.pk, "main", rnd(0, 0, "", "retired", reason="второе неповиновение"))
        r = Result.objects.get(entry=self.e1)
        self.assertEqual(scoring.result_cells(self.p1, r)[0], "Сошёл")
        # причина исключения не вводится и не печатается (решение заказчика 22.10.2026)
        scoring.save_round(self.judge, self.e2.pk, "main", rnd(0, 2, "", "eliminated", reason="что угодно"))
        self.assertEqual(scoring.result_cells(self.p1, Result.objects.get(entry=self.e2))[0], "Искл.")

    @override_settings(DEBUG=False)
    def test_k5_russian_error_pages(self):
        resp = self.client.get("/t/999999/")
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, "Страница не найдена", status_code=404)
        self.client.force_login(self.other)
        resp = self.client.get(reverse("entries:manage", args=[self.t.pk]))
        self.assertContains(resp, "Нет доступа", status_code=403)
        resp = self.client.get(reverse("entries:manage", args=[self.t.pk]), HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.coach)
        resp = c.post(reverse("profiles:athlete_new"), {"last_name": "Х"})
        self.assertContains(resp, "Страница устарела", status_code=403)


@override_settings(MEDIA_ROOT=TMP)
class MergeClassesTests(ConductBase):
    def test_merge_shares_places_notifies_and_locks_after_publication(self):
        from apps.core.models import Notification

        self.p1.program_type, self.p1.table, self.p1.result_fields = "clear_speed", "A", []
        self.p1.save()
        services.merge_classes(self.judge, self.c_kids.pk, self.c_open.pk)
        self.c_kids.refresh_from_db()
        self.assertEqual(self.c_kids.merged_into_id, self.c_open.pk)
        self.assertEqual(scoring.group_key(Entry.objects.get(pk=self.e1.pk)), scoring.group_key(Entry.objects.get(pk=self.e2.pk)))
        self.assertTrue(Notification.objects.filter(kind="merge", user=self.coach).exists())
        services.draw(self.judge, self.p1.pk)
        scoring.save_course(self.judge, self.p1, {"main_length": "350", "main_speed": "350"})
        scoring.save_round(self.judge, self.e1.pk, "main", rnd(0, 0, "55"))
        scoring.save_round(self.judge, self.e2.pk, "main", rnd(0, 0, "50"))
        places = dict(Result.objects.filter(entry__in=[self.e1, self.e2]).values_list("entry_id", "place"))
        self.assertEqual(places, {self.e2.pk: 1, self.e1.pk: 2})  # общие места
        with self.assertRaises(ValueError):
            services.publish_results(self.judge, self.c_kids.pk)  # публикуется объединённый зачёт
        services.publish_start_list(self.judge, self.p1.pk)
        with self.assertRaisesMessage(ValueError, "уже опубликован"):
            services.unmerge_class(self.judge, self.c_kids.pk)

    def test_merge_rules_and_permissions(self):
        with self.assertRaises(Exception):
            services.merge_classes(self.other, self.c_kids.pk, self.c_open.pk)
        with self.assertRaisesMessage(ValueError, "одного маршрута"):
            services.merge_classes(self.judge, self.c_kids.pk, self.c_kids.pk)
        self.client.force_login(self.judge)
        resp = self.client.post(reverse("conduct:action", args=[self.t.pk, self.p1.pk, "merge"]), {"tclass": self.c_kids.pk, "into": self.c_open.pk})
        self.assertEqual(resp.status_code, 302)
        self.client.post(reverse("conduct:action", args=[self.t.pk, self.p1.pk, "unmerge"]), {"tclass": self.c_kids.pk})
        self.c_kids.refresh_from_db()
        self.assertIsNone(self.c_kids.merged_into_id)
