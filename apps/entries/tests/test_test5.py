"""Отчёт тестирования 5 (27.10.2026): единый разряд, экран заявки на телефоне, заявка по всадникам, «Мои заявки»."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.entries import services
from apps.entries.models import ApplicationDraft
from apps.entries.tests.test_entries import TMP, Base
from apps.tournaments.models import ProgramItem, TournamentClass


@override_settings(MEDIA_ROOT=TMP)
class SingleRankTests(Base):
    def test_profile_form_has_one_rank_field(self):
        self.client.force_login(self.coach)
        page = self.client.get(reverse("profiles:athlete_edit", args=[self.a2.pk])).content.decode()
        self.assertIn('name="rank"', page)
        self.assertNotIn("rank_jumping", page)
        self.assertNotIn("rank_dressage", page)
        self.client.post(reverse("profiles:athlete_edit", args=[self.a2.pk]), {"last_name": "Орлов", "first_name": "Дима", "birth_date": "1996-01-01", "rank": "kms"})
        self.a2.refresh_from_db()
        self.assertEqual(self.a2.rank, "kms")
        self.assertEqual(self.a2.rank_short, "КМС")

    def test_same_rank_for_jumping_and_dressage(self):
        dr = ProgramItem.objects.create(tournament=self.t, day=self.start, discipline="dressage", name="Езда", program_type="manual", result_fields=["percent"])
        c_dr = TournamentClass.objects.create(program=dr, name="Взрослые", rank_rule="min", rank="1")
        self.c_open.rank_rule, self.c_open.rank = "min", "1"
        self.c_open.save()
        year = self.start.year
        self.a2.rank = "2"
        self.assertTrue(services.class_reasons(self.a2, self.h1, self.c_open, year))
        self.assertTrue(services.class_reasons(self.a2, self.h1, c_dr, year))
        self.a2.rank = "kms"
        self.assertEqual(services.class_reasons(self.a2, self.h1, self.c_open, year), [])
        self.assertEqual(services.class_reasons(self.a2, self.h1, c_dr, year), [])


@override_settings(MEDIA_ROOT=TMP)
class NeverRuleTests(Base):
    """«Не выполнявшие норматив … и выше»: при известном разряде решаем сами, без галочки."""

    def setUp(self):
        super().setUp()
        self.c_open.rank_rule, self.c_open.rank = "never", "1"
        self.c_open.save()
        self.year = self.start.year
        self.url = reverse("entries:apply", args=[self.t.pk])

    def test_known_low_rank_eligible_without_confirmation(self):
        self.a2.rank = "2"
        self.assertEqual(services.class_reasons(self.a2, self.h1, self.c_open, self.year, confirmed=False), [])

    def test_known_high_rank_not_eligible(self):
        self.a2.rank = "kms"
        why = services.class_reasons(self.a2, self.h1, self.c_open, self.year, confirmed=True)
        self.assertTrue(why and "КМС" in why[0])

    def test_unknown_rank_needs_confirmation(self):
        self.a2.rank = ""
        self.assertTrue(services.class_reasons(self.a2, self.h1, self.c_open, self.year, confirmed=False))
        self.assertEqual(services.class_reasons(self.a2, self.h1, self.c_open, self.year, confirmed=True), [])

    def test_apply_page_checkbox_only_without_rank(self):
        self.client.force_login(self.coach)
        self.client.post(self.url, {"action": "pair_add", "athlete": self.a2.pk, "horse": self.h1.pk})
        page = self.client.get(self.url + f"?open={self.a2.pk}-{self.h1.pk}").content.decode()
        self.assertIn(f'name="confirm_{self.p1.pk}"', page)
        self.a2.rank = "3"
        self.a2.save()
        page = self.client.get(self.url + f"?open={self.a2.pk}-{self.h1.pk}").content.decode()
        self.assertNotIn(f'name="confirm_{self.p1.pk}"', page)
        # добавляется без подтверждения
        resp = self.client.post(self.url, {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk, f"sel_{self.p1.pk}": str(self.c_open.pk)})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(ApplicationDraft.objects.get(user=self.coach).data["lines"]), 1)
        # старший разряд — зачёт закрыт, причина видна
        ApplicationDraft.objects.filter(user=self.coach).delete()
        self.client.post(self.url, {"action": "pair_add", "athlete": self.a2.pk, "horse": self.h1.pk})
        self.a2.rank = "kms"
        self.a2.save()
        page = self.client.get(self.url + f"?open={self.a2.pk}-{self.h1.pk}").content.decode()
        self.assertIn("у всадника разряд КМС", page)


@override_settings(MEDIA_ROOT=TMP)
class ApplyScreenTests(Base):
    def setUp(self):
        super().setUp()
        self.p2 = ProgramItem.objects.create(tournament=self.t, day=self.start, discipline="jumping", name="Маршрут №2 · 100 см",
                                             program_type="manual", result_fields=["penalties"], heights=[100])
        self.c2 = TournamentClass.objects.create(program=self.p2, name="Любители")
        self.url = reverse("entries:apply", args=[self.t.pk])
        self.client.force_login(self.coach)
        self.client.post(self.url, {"action": "pair_add", "athlete": self.a2.pk, "horse": self.h1.pk})

    def add_two(self):
        return self.client.post(self.url, {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk,
                                           f"sel_{self.p1.pk}": str(self.c_open.pk), f"sel_{self.p2.pk}": str(self.c2.pk)})

    def test_hint_removed_and_height_not_repeated(self):
        page = self.client.get(self.url).content.decode()
        self.assertNotIn("Отметьте зачёт в нужных маршрутах", page)
        import re
        heads = re.findall(r'<strong>(Маршрут №\d[^<]*)</strong>\s*(?:\{#.*?#\}\s*)?<span class="table__sub">([^<]*)</span>', page, re.S)
        heads = {name: sub for name, sub in heads}
        self.assertNotIn("100 см", heads["Маршрут №2 · 100 см"])  # высота уже в названии — в подписи не повторяется
        self.assertIn("80 см", heads["Маршрут №1"])  # нет в названии — остаётся в подписи

    def test_after_add_pair_collapses_with_summary(self):
        resp = self.add_two()
        self.assertEqual(resp.status_code, 302)
        page = self.client.get(resp["Location"]).content.decode()
        self.assertIn("pair-summary--routes", page)
        self.assertNotIn('value="add_many"', page)  # выбор свёрнут
        self.assertIn("Любители", page)
        self.assertIn("Изменить", page)

    def test_remove_one_class_updates_screen(self):
        self.add_two()
        opened = f"?open={self.a2.pk}-{self.h1.pk}"
        page = self.client.get(self.url + opened).content.decode()
        self.assertIn(f'name="remove" value="{self.c2.pk}|"', page)
        # HTMX-запрос из карточки пары: редирект, затем свежая карточка без убранного зачёта
        resp = self.client.post(self.url, {"late": "0", "a": self.a2.pk, "h": self.h1.pk, "open": f"{self.a2.pk}-{self.h1.pk}",
                                           "remove": f"{self.c2.pk}|"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 302)
        lines = ApplicationDraft.objects.get(user=self.coach).data["lines"]
        self.assertEqual([r["c"] for r in lines], [self.c_open.pk])
        page = self.client.get(resp["Location"], HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn(f'name="remove" value="{self.c2.pk}|"', page)
        self.assertIn(f'name="remove" value="{self.c_open.pk}|"', page)
        self.assertIn('id="apply-count"', page)
        # убранный зачёт снова можно выбрать
        self.assertIn(f'name="sel_{self.p2.pk}" value="{self.c2.pk}"', page)

    def test_remove_with_stale_key_is_harmless(self):
        self.add_two()
        self.client.post(self.url, {"a": self.a2.pk, "h": self.h1.pk, "remove": f"{self.c2.pk}|"})
        self.client.post(self.url, {"a": self.a2.pk, "h": self.h1.pk, "remove": f"{self.c2.pk}|"})  # повтор
        self.assertEqual([r["c"] for r in ApplicationDraft.objects.get(user=self.coach).data["lines"]], [self.c_open.pk])

    def test_pick_errors_shown_inside_pair(self):
        resp = self.client.post(self.url, {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk})
        page = resp.content.decode()
        start = page.index(f'id="pair-{self.a2.pk}-{self.h1.pk}"')
        self.assertIn("Выберите зачёт", page[start:])

    def test_cancel_terms_near_submit(self):
        snap = dict(self.t.snapshot or {})
        snap["cancel_terms"] = "Отмена без потерь — до дня окончания приёма заявок."
        self.t.snapshot = snap
        self.t.save()
        page = self.client.get(self.url).content.decode()
        self.assertIn("Условия отмены", page)
        self.assertLess(page.index("Условия отмены"), page.index('value="submit"'))

    def test_guest_redirected_to_login(self):
        self.client.logout()
        resp = self.client.post(self.url, {"a": self.a2.pk, "h": self.h1.pk, "remove": f"{self.c2.pk}|"}, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_cannot_touch_someone_elses_draft(self):
        self.add_two()
        self.client.force_login(self.other)
        self.client.post(self.url, {"a": self.a2.pk, "h": self.h1.pk, "remove": f"{self.c2.pk}|"})
        # черновик тренера не изменился: у другого пользователя — свой черновик
        self.assertEqual(len(ApplicationDraft.objects.get(user=self.coach).data["lines"]), 2)
        # чужой спортсмен в свою заявку не добавляется
        self.client.post(self.url, {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk, f"sel_{self.p1.pk}": str(self.c_open.pk)})
        d = ApplicationDraft.objects.filter(user=self.other).first()
        self.assertFalse(d and d.data.get("lines"))


@override_settings(MEDIA_ROOT=TMP)
class ApplicationGroupsTests(Base):
    def test_entries_grouped_by_rider_and_bulk_cancel_kept(self):
        app = self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids), services.Line(self.a2, self.h1, self.c_open)])
        self.client.force_login(self.coach)
        page = self.client.get(reverse("entries:application", args=[app.pk])).content.decode()
        # тест 5.1: сначала день соревнований, внутри — всадники
        self.assertEqual(page.count('class="app-day"'), 1)
        self.assertEqual(page.count('class="app-rider"'), 2)
        self.assertIn("Отменить выбранные", page)
        self.assertEqual(page.count('name="entry"'), 2)

    def test_application_closed_for_guest_and_stranger(self):
        app = self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        url = reverse("entries:application", args=[app.pk])
        self.assertIn("login", self.client.get(url)["Location"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)


@override_settings(MEDIA_ROOT=TMP)
class MyListsTests(Base):
    def test_old_lists_lead_to_my_competitions(self):
        """Тест 5.1: «Мои заявки» и «Мои выступления» — это «Мои соревнования»; старые адреса работают."""
        comp = reverse("entries:competitions")
        self.assertIn("login", self.client.get(reverse("entries:mine"))["Location"])  # гость → вход
        self.client.force_login(self.coach)
        self.assertRedirects(self.client.get(reverse("entries:mine")), comp, fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("entries:mine") + "?q=Барон"), comp + "?q=%D0%91%D0%B0%D1%80%D0%BE%D0%BD", fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("entries:my_starts")), comp, fetch_redirect_response=False)
        page = self.client.get(comp + "?q=Барон").content.decode()
        self.assertIn('value="Барон"', page)  # поиск переносится
        self.assertIn(f'href="{reverse("core:calendar")}"', page)
        self.assertIn("Найти соревнования", page)


class RankMergeMigrationTests(TransactionTestCase):
    """Перенос двух разрядов в один: берётся старший."""

    old = [("profiles", "0005_phone_any_country")]
    new = [("profiles", "0006_single_rank")]

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_higher_rank_kept(self):
        ex = MigrationExecutor(connection)
        ex.migrate(self.old)
        apps = ex.loader.project_state(self.old).apps
        A = apps.get_model("profiles", "Athlete")
        from apps.accounts.models import User

        u = User.objects.create_user("+79990009999", "Konkur-2026!", last_name="Т", first_name="Ю")
        rows = {
            "a": ("2", "kms"), "b": ("1", "3y"), "c": ("", "3"), "d": ("", ""), "e": ("none", ""),
        }
        for name, (j, d) in rows.items():
            A.objects.create(last_name=name, first_name="x", manager_id=u.pk, rank_jumping=j, rank_dressage=d)
        ex = MigrationExecutor(connection)
        ex.migrate(self.new)
        A = ex.loader.project_state(self.new).apps.get_model("profiles", "Athlete")
        got = dict(A.objects.values_list("last_name", "rank"))
        self.assertEqual(got, {"a": "kms", "b": "1", "c": "3", "d": "", "e": "none"})
