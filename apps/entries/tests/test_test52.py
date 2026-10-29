"""Тест 5.2 (29.10.2026): подача заявки (поданные зачёты пары, ошибка у кнопки), экран заявки, изменение выступления."""

import json

from django.test import override_settings
from django.urls import reverse

from apps.entries import services
from apps.entries.models import ApplicationDraft, ChangeProposal, Entry
from apps.entries.tests.test_entries import TMP, Base
from apps.profiles.models import Horse


@override_settings(MEDIA_ROOT=TMP)
class ApplyFiledTests(Base):
    """п.35: зачёты, в которые пара уже заявлена (в поданных заявках), видны при выборе."""

    def setUp(self):
        super().setUp()
        self.app = self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids)])
        self.url = reverse("entries:apply", args=[self.t.pk])
        self.client.force_login(self.coach)
        self.client.post(self.url, {"action": "pair_add", "athlete": self.a1.pk, "horse": self.h1.pk})

    def page(self):
        return self.client.get(self.url + f"?open={self.a1.pk}-{self.h1.pk}").content.decode()

    def test_filed_class_marked_and_other_class_closed(self):
        page = self.page()
        self.assertIn("Пара уже заявлена в зачёте «Дети»", page)
        self.assertIn(f"Уже заявлены · заявка №{self.app.number}", page)
        self.assertNotIn(f'name="sel_{self.p1.pk}" value="{self.c_kids.pk}"', page)
        # в маршруте пара выступает в одном зачёте — «Открытый» закрыт с причиной
        self.assertIn(f'name="sel_{self.p1.pk}" value="{self.c_open.pk}" disabled', page)
        self.assertIn("пара уже заявлена в этом маршруте в другом зачёте", page)

    def test_cancelled_entry_does_not_block(self):
        Entry.objects.filter(application=self.app).update(decision=Entry.Decision.CANCELLED)
        page = self.page()
        self.assertNotIn("Уже заявлены", page)
        self.assertIn(f'name="sel_{self.p1.pk}" value="{self.c_open.pk}">', page)

    def test_add_other_class_rejected_with_error_near_button(self):
        r = self.client.post(self.url, {"action": "add_many", "a": self.a1.pk, "h": self.h1.pk, "open": f"{self.a1.pk}-{self.h1.pk}",
                                        f"sel_{self.p1.pk}": str(self.c_open.pk)})
        page = r.content.decode()
        self.assertIn("уже заявлена в зачёте «Дети»", page)
        # п.34: ошибка — alert--error над кнопкой «Добавить выбранные», форма ведёт к ней якорем
        err = page.index('class="alert alert--error ap-pair__err"')
        self.assertLess(err, page.index("data-add-many"))
        self.assertGreater(err, page.index(f'name="sel_{self.p1.pk}"'))
        self.assertIn(f'#pair-err-{self.a1.pk}-{self.h1.pk}"', page)
        self.assertEqual(ApplicationDraft.objects.get(user=self.coach).data["lines"], [])


@override_settings(MEDIA_ROOT=TMP)
class ApplicationScreenTests(Base):
    def setUp(self):
        super().setUp()
        self.h3 = Horse.objects.create(name="Гранит", birth_year=2012, color="grey", sex="gelding", breed="орловская", manager=self.coach)
        self.app = self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        self.e = self.app.entries.get()
        self.url = reverse("entries:application", args=[self.app.pk])
        self.purl = reverse("entries:propose", args=[self.e.pk])

    def test_head_summary_and_contacts_pencil(self):
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        # п.37: сводка в строке заголовка
        head = page.index('class="ae-head__title"')
        self.assertLess(head, page.index("ae-head__chips"))
        self.assertLess(page.index("ae-head__chips"), page.index('class="ae-head__t"'))
        # п.41: «Предложить замену» убрана; п.44: карандаш у «Контактов», форма — по нему
        self.assertNotIn("Предложить замену", page)
        self.assertIn('href="?contacts=1#contacts"', page)
        self.assertNotIn(reverse("entries:contacts", args=[self.app.pk]), page)
        page = self.client.get(self.url + "?contacts=1").content.decode()
        self.assertIn(reverse("entries:contacts", args=[self.app.pk]), page)

    def test_edit_mode_has_change_link(self):
        self.client.force_login(self.coach)
        page = self.client.get(self.url + "?edit=1").content.decode()
        self.assertIn(f'href="{self.purl}"', page)
        ChangeProposal.objects.create(entry=self.e, horse=self.h3, created_by=self.coach)
        page = self.client.get(self.url + "?edit=1").content.decode()
        self.assertNotIn(f'href="{self.purl}"', page)
        self.assertIn("Изменение у судьи", page)

    def test_stranger_sees_no_contacts_form(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url + "?contacts=1").status_code, 403)

    def test_change_screen_cards_and_proposal(self):
        self.client.force_login(self.coach)
        page = self.client.get(self.purl).content.decode()
        self.assertIn("Изменить выступление", page)
        self.assertIn(f'type="radio" name="tclass" value="{self.c_open.pk}" checked', page)
        self.assertIn("Сейчас", page)
        # другая лошадь — тот же зачёт: предложение изменения судье, данные пока прежние
        r = self.client.post(self.purl, {"athlete": self.a2.pk, "horse": self.h3.pk, "tclass": self.c_open.pk})
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        p = ChangeProposal.objects.get(entry=self.e)
        self.assertEqual((p.horse_id, p.tclass_id, p.athlete_id), (self.h3.pk, None, None))
        self.e.refresh_from_db()
        self.assertEqual(self.e.horse_id, self.h1.pk)
        # второе — только после решения судьи
        page = self.client.get(self.purl).content.decode()
        self.assertIn("уже есть изменение на рассмотрении", page)
        self.client.post(self.purl, {"athlete": self.a2.pk, "horse": self.h1.pk, "tclass": self.c_kids.pk})
        self.assertEqual(ChangeProposal.objects.filter(entry=self.e).count(), 1)

    def test_change_screen_marks_filed_and_blocks_second_class(self):
        self.submit(self.coach, [services.Line(self.a2, self.h3, self.c_open)], key="k2")
        self.client.force_login(self.coach)
        page = self.client.get(self.purl + f"?athlete={self.a2.pk}&horse={self.h3.pk}").content.decode()
        self.assertIn("Уже заявлены", page)
        r = self.client.post(self.purl, {"athlete": self.a2.pk, "horse": self.h3.pk, "tclass": self.c_open.pk})
        self.assertEqual(r.status_code, 200)
        self.assertIn("alert--error", r.content.decode())
        self.assertFalse(ChangeProposal.objects.exists())

    def test_change_screen_guest_and_stranger(self):
        self.assertIn("login", self.client.get(self.purl)["Location"])
        self.assertIn("login", self.client.post(self.purl, {"horse": self.h3.pk})["Location"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.purl).status_code, 403)
        self.assertEqual(self.client.post(self.purl, {"athlete": self.a2.pk, "horse": self.h3.pk, "tclass": self.c_open.pk}).status_code, 403)
        self.assertFalse(ChangeProposal.objects.exists())

    def test_change_closed_for_cancelled(self):
        Entry.objects.filter(pk=self.e.pk).update(decision=Entry.Decision.CANCELLED)
        self.client.force_login(self.coach)
        r = self.client.get(self.purl)
        self.assertRedirects(r, self.url + "?edit=1", fetch_redirect_response=False)


@override_settings(MEDIA_ROOT=TMP)
class MyCompetitionsDatesTests(Base):
    def test_no_calendar_button_and_dates_prepared(self):
        self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open)])
        self.client.force_login(self.coach)
        r = self.client.get(reverse("entries:competitions"))
        page = r.content.decode()
        self.assertNotIn("mc-find", page)
        self.assertEqual(r.context["calendar_dates"], {"upcoming": [self.start.isoformat()], "past": []})
        self.assertIn('id="mc-dates"', page)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("entries:competitions")).context["calendar_dates"], {"upcoming": [], "past": []})



@override_settings(MEDIA_ROOT=TMP)
class ApplicationNumberTests(Base):
    """Решение 29.10.2026: заявки нумеруются с №1 внутри каждого турнира."""

    def test_numbers_start_from_one_per_tournament(self):
        from apps.entries.models import Application

        t2 = self.make_tournament()
        mk = lambda t, k: Application.objects.create(tournament=t, submitted_by=self.coach, idempotency_key=k, content_hash="x")
        a1, a2, b1 = mk(self.t, "k1"), mk(self.t, "k2"), mk(t2, "k3")
        self.assertEqual((a1.number, a2.number, b1.number), (1, 2, 1))
        self.assertEqual(str(a2), "Заявка №2")
        self.client.force_login(self.coach)
        self.assertContains(self.client.get(reverse("entries:application", args=[a2.pk])), "Заявка №2")
