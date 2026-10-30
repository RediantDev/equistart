"""Тест 6 (29.10.2026): отзыв с проверки, автосохранение черновика, переход на панель после отправки."""
import datetime

from django.urls import reverse

from apps.core.models import AuditLog
from apps.tournaments.models import TeamMember, Tournament

from .test_tournaments import Base, user


class WithdrawTests(Base):
    def submitted(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        resp = self.client.post(reverse("tournaments:step", args=[t.pk, 4]), {"revision": t.revision})
        self.assertRedirects(resp, reverse("tournaments:hub", args=[t.pk]), fetch_redirect_response=False)  # п. 23
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.REVIEW)
        return t

    def test_owner_withdraws_and_resubmits(self):
        t = self.submitted()
        # на проверке шаги 1–3 — только просмотр сводкой (п. 27)
        self.assertRedirects(self.client.get(reverse("tournaments:step", args=[t.pk, 1])), reverse("tournaments:step", args=[t.pk, 4]))
        self.assertContains(self.client.get(reverse("tournaments:step", args=[t.pk, 4])), "Отозвать и изменить")
        resp = self.client.post(reverse("tournaments:withdraw", args=[t.pk]), {"revision": t.revision})
        self.assertRedirects(resp, reverse("tournaments:step", args=[t.pk, 1]), fetch_redirect_response=False)
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.DRAFT)
        self.assertTrue(AuditLog.objects.filter(action="tournament.withdraw", target_id=t.pk).exists())
        self.client.post(reverse("tournaments:step", args=[t.pk, 4]), {"revision": t.revision})
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.REVIEW)

    def test_stale_revision_and_not_in_review(self):
        t = self.submitted()
        self.client.post(reverse("tournaments:withdraw", args=[t.pk]), {"revision": t.revision - 1})
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.REVIEW)
        self.client.post(reverse("tournaments:withdraw", args=[t.pk]), {"revision": t.revision})
        t.refresh_from_db()
        rev = t.revision
        self.client.post(reverse("tournaments:withdraw", args=[t.pk]), {"revision": t.revision})  # уже черновик
        t.refresh_from_db()
        self.assertEqual((t.status, t.revision), (Tournament.Status.DRAFT, rev))

    def test_guest_stranger_judge_closed(self):
        t = self.submitted()
        url = reverse("tournaments:withdraw", args=[t.pk])
        self.client.logout()
        self.assertEqual(self.client.post(url, {"revision": t.revision}).status_code, 302)
        judge = user("+79990000199", is_judge=True)
        TeamMember.objects.create(tournament=t, user=judge, phone=judge.phone, role=TeamMember.Role.JUDGE, state=TeamMember.State.ACCEPTED, invited_by=self.org)
        for who in (self.other, judge):
            self.client.force_login(who)
            self.assertEqual(self.client.post(url, {"revision": t.revision}).status_code, 403)
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.REVIEW)


class AutosaveTests(Base):
    def test_create_needs_title_and_makes_one_draft(self):
        self.client.force_login(self.org)
        url = reverse("tournaments:create")
        r = self.client.post(url, {"autosave": "1", "create_key": "k1", "title": ""})
        self.assertFalse(r.json()["ok"])
        self.assertFalse(Tournament.objects.exists())
        r1 = self.client.post(url, {"autosave": "1", "create_key": "k1", "title": "Осень"}).json()
        r2 = self.client.post(url, {"autosave": "1", "create_key": "k1", "title": "Осенний приз", "starts_on": self.start.isoformat()}).json()
        self.assertEqual(Tournament.objects.count(), 1)
        self.assertEqual(r1["pk"], r2["pk"])
        t = Tournament.objects.get()
        self.assertEqual((t.title, t.starts_on, t.ends_on), ("Осенний приз", self.start, self.start))
        self.assertEqual(t.status, Tournament.Status.DRAFT)

    def test_step1_autosave_revision_lock_and_dates(self):
        t = self.make_ready(days=3)
        self.client.force_login(self.org)
        url = reverse("tournaments:step", args=[t.pk, 1])
        base = {"autosave": "1", "title": t.title, "starts_on": t.starts_on.isoformat(), "multiday": "on", "ends_on": t.ends_on.isoformat()}
        self.assertEqual(self.client.post(url, {**base, "revision": t.revision - 1}).status_code, 409)
        # дату начала сдвинули позже окончания — окончание подтягивается, без ошибки базы
        later = (t.ends_on + datetime.timedelta(days=5)).isoformat()
        r = self.client.post(url, {**base, "revision": t.revision, "starts_on": later})
        self.assertEqual(r.status_code, 200)
        t.refresh_from_db()
        self.assertGreaterEqual(t.ends_on, t.starts_on)
        t.status = Tournament.Status.REVIEW
        t.save()
        self.assertEqual(self.client.post(url, {**base, "revision": t.revision}).status_code, 409)

    def test_step3_autosave_and_stranger(self):
        t = self.make_ready()
        url = reverse("tournaments:step", args=[t.pk, 3])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"autosave": "1", "revision": t.revision, "cancel_terms": "Х"}).status_code, 403)
        self.client.force_login(self.org)
        r = self.client.post(url, {"autosave": "1", "revision": t.revision, "cancel_terms": "Новые условия",
                                   "entry_deadline": t.entry_deadline.isoformat()}).json()
        self.assertTrue(r["ok"] and r["saved"])
        t.refresh_from_db()
        self.assertEqual(t.cancel_terms, "Новые условия")
        self.assertTrue(t.regulation)  # файл не тронут


class AutosaveThenNextTests(Base):
    def test_autosave_then_next_is_one_tournament(self):
        """Тест 6.1, п. 17: автосохранение и затем «Далее» с тем же ключом — один черновик."""
        self.client.force_login(self.org)
        url = reverse("tournaments:create")
        self.client.post(url, {"autosave": "1", "create_key": "k9", "title": "Кубок главы"})
        venue = self.make_ready().venue
        n = Tournament.objects.count()
        resp = self.client.post(url, {"create_key": "k9", "next": "1", "title": "Кубок главы", "starts_on": self.start.isoformat(),
                                      "venue": venue.pk, "contact_name": "КСК", "contact": "+7 900 000-00-00", "level": "club"})
        self.assertEqual(Tournament.objects.count(), n)
        t = Tournament.objects.filter(title="Кубок главы").get()
        self.assertRedirects(resp, reverse("tournaments:step", args=[t.pk, 2]), fetch_redirect_response=False)


class MyListFiltersTests(Base):
    """Тест 6.1, п. 9: поиск и фильтры в «Мои соревнования», прошедшие — отдельной вкладкой; чужие не видны."""

    def test_search_filters_and_past(self):
        self.client.force_login(self.org)
        today = datetime.date.today()
        Tournament.objects.create(owner=self.org, title="Кубок главы", starts_on=today + datetime.timedelta(days=20), ends_on=today + datetime.timedelta(days=21))
        Tournament.objects.create(owner=self.org, title="Осенний старт", starts_on=today - datetime.timedelta(days=40), ends_on=today - datetime.timedelta(days=40))
        other = user("+79990007777")
        Tournament.objects.create(owner=other, title="Чужой кубок", starts_on=today + datetime.timedelta(days=5), ends_on=today + datetime.timedelta(days=5))
        url = reverse("tournaments:mine")
        r = self.client.get(url)
        self.assertContains(r, "Кубок главы")
        self.assertNotContains(r, "Осенний старт")
        self.assertNotContains(r, "Чужой кубок")
        self.assertContains(self.client.get(url + "?when=past"), "Осенний старт")
        r = self.client.get(url + "?q=кубок")
        self.assertContains(r, "Кубок главы")
        self.assertNotContains(r, "Чужой кубок")
        r = self.client.get(url + "?status=finished")
        self.assertContains(r, "Ничего не найдено")
        self.assertEqual(self.client.get(url + "?status=bogus&year=abc&venue=x").status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)


class ProgramRequiresClassTests(Base):
    """Тест 6.1, п. 13: без зачёта и без таблицы штрафов маршрут не сохраняется."""

    def test_no_class_no_table(self):
        t = self.make_ready()
        self.client.force_login(self.org)
        data = {"revision": t.revision, "day": self.start.isoformat(), "discipline": "jumping", "program_type": "no_time", "heights_text": "80"}
        resp = self.client.post(reverse("tournaments:program_add", args=[t.pk]), data)
        self.assertEqual(resp.status_code, 422)
        self.assertContains(resp, "Выберите хотя бы один зачёт", status_code=422)
        self.assertContains(resp, "Выберите таблицу штрафов", status_code=422)
        self.assertEqual(t.programs.count(), 1)
