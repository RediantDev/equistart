"""Тест 4 (23.10.2026): порядок «Стартует / Не стартует», автодобавление принятых, правка пары, быстрое добавление судьёй,
жеребьёвка меняет порядок, публикация только при наличии результатов, экран заявок, профиль."""

from django.test import override_settings
from django.urls import reverse

from apps.conduct import services
from apps.conduct.models import StartSlot
from apps.entries import services as eservices
from apps.entries.models import Entry
from apps.entries.tests.test_entries import TMP
from apps.profiles.models import Athlete, Horse

from .test_conduct import ConductBase


@override_settings(MEDIA_ROOT=TMP)
class Test4ConductTests(ConductBase):
    def test_participation_toggle(self):
        services.draw(self.judge, self.p1.pk)
        s = StartSlot.objects.get(entry=self.e1)
        services.set_participation(self.judge, s.pk, False)
        s.refresh_from_db()
        self.assertTrue(s.is_off)
        services.set_participation(self.judge, s.pk, True)
        s.refresh_from_db()
        self.assertEqual(s.status, StartSlot.Status.WAITING)

    def test_accepted_after_draw_goes_to_end(self):
        services.draw(self.judge, self.p1.pk)
        app = self.submit(self.coach, [eservices.Line(self.a1, self.h3, self.c_open)], key="late1")
        e = app.entries.get()
        self.accept(e)
        slot = StartSlot.objects.get(entry=e)
        self.assertEqual(slot.position, 3)

    def test_draw_changes_order(self):
        app = self.submit(self.coach, [eservices.Line(self.a1, self.h3, self.c_open)], key="d3")
        self.accept(app.entries.get())
        services.draw(self.judge, self.p1.pk)
        before = list(self.p1.slots.order_by("position").values_list("pk", flat=True))
        r = services.draw(self.judge, self.p1.pk)
        after = list(self.p1.slots.order_by("position").values_list("pk", flat=True))
        self.assertEqual(r["shuffled"], 3)
        self.assertNotEqual(before, after)

    def test_edit_pair_snapshot_only(self):
        services.edit_pair(self.judge, self.e1.pk, {"name": "Иванова Анна", "birth_year": 2010}, {"name": "Буран"})
        self.e1.refresh_from_db()
        self.assertEqual(self.e1.athlete_snapshot["name"], "Иванова Анна")
        self.assertEqual(self.e1.horse_snapshot["name"], "Буран")
        self.assertNotEqual(self.e1.athlete.last_name, "Иванова")

    def test_pair_edit_view_requires_judge(self):
        url = reverse("conduct:pair_edit", args=[self.t.pk, self.p1.pk, self.e1.pk])
        self.client.force_login(self.coach)
        self.assertIn(self.client.get(url).status_code, (403, 404))
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_program_tabs_render(self):
        services.draw(self.judge, self.p1.pk)
        self.client.force_login(self.judge)
        for tab in ("order", "course", "results", "merge"):
            with self.subTest(tab=tab):
                r = self.client.get(reverse("conduct:program", args=[self.t.pk, self.p1.pk]) + f"?tab={tab}")
                self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Добавить готовых в конец")

    def test_no_show_is_not_a_result_to_publish(self):
        from apps.conduct.models import Result

        r = Result(entry=self.e1, status=Result.Status.NO_SHOW)
        self.assertFalse(services.has_real_result(r))
        self.assertTrue(services.has_real_result(Result(entry=self.e1, place=1)))


@override_settings(MEDIA_ROOT=TMP)
class Test4EntriesTests(ConductBase):
    def test_quick_add_creates_accepted_entry_not_in_judge_lists(self):
        self.client.force_login(self.judge)
        url = reverse("entries:quick_add", args=[self.t.pk])
        data = {"last_name": "Новикова", "first_name": "Вера", "birth_date": "2012-03-04", "rank": "none",
                "horse_name": "Тайфун", "horse_year": "2015", "classes": [str(self.c_open.pk)]}
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 302)
        e = Entry.objects.get(athlete__last_name="Новикова")
        self.assertEqual(e.decision, Entry.Decision.ACCEPTED)
        self.assertTrue(e.athlete.added_by_judge and e.horse.added_by_judge)
        from apps.profiles.services import athletes_for_entry

        self.assertFalse(athletes_for_entry(self.judge).filter(pk=e.athlete_id).exists())

    def test_quick_add_and_search_forbidden_for_others(self):
        for url in (reverse("entries:quick_add", args=[self.t.pk]), reverse("entries:quick_search", args=[self.t.pk]) + "?q=Ки"):
            self.client.force_login(self.coach)
            self.assertIn(self.client.get(url).status_code, (403, 404))
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_manage_tabs_and_single_reject(self):
        app = self.submit(self.coach, [eservices.Line(self.a1, self.h3, self.c_open)], key="m1")
        e = app.entries.get()
        self.client.force_login(self.judge)
        r = self.client.get(reverse("entries:manage", args=[self.t.pk]))
        self.assertContains(r, "На рассмотрении")
        self.assertNotContains(r, "Все на странице")
        self.client.post(reverse("entries:decide", args=[self.t.pk]), {"confirm": "1", "action": "reject", "items": f"{e.pk}:{e.revision}", "reason": "возраст"})
        e.refresh_from_db()
        self.assertEqual(e.decision, Entry.Decision.REJECTED)


class Test4ProfileTests(ConductBase):
    def test_profile_and_role_removal(self):
        self.client.force_login(self.coach)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)
        self.coach.is_judge = True
        self.coach.save()
        self.client.post(reverse("accounts:remove_role"), {"category": "judge"})
        self.coach.refresh_from_db()
        self.assertFalse(self.coach.is_judge)

    def test_cannot_remove_last_role(self):
        for c in ("athlete", "coach", "organizer", "judge", "club"):
            setattr(self.coach, f"is_{c}", c == "coach")
        self.coach.save()
        self.client.force_login(self.coach)
        self.client.post(reverse("accounts:remove_role"), {"category": "coach"})
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.is_coach)
