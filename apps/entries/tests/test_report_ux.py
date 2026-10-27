"""Удобство подачи заявки и уведомлений (отчёт тестирования 22.10.2026)."""

from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Notification
from apps.entries import services
from apps.entries.models import ApplicationDraft, Entry
from apps.entries.tests.test_entries import TMP, Base
from apps.profiles.models import Athlete


@override_settings(MEDIA_ROOT=TMP)
class ApplyUxTests(Base):
    def url(self):
        return reverse("entries:apply", args=[self.t.pk])

    def test_draft_survives_logout_and_many_picks_in_one_click(self):
        self.client.force_login(self.coach)
        self.client.post(self.url(), {"action": "pair_add", "athlete": self.a1.pk, "horse": self.h1.pk})
        self.client.post(self.url(), {"action": "pair_add", "athlete": self.a2.pk, "horse": self.h1.pk})
        self.assertEqual(ApplicationDraft.objects.get(user=self.coach).data["pairs"], [[self.a1.pk, self.h1.pk], [self.a2.pk, self.h1.pk]])
        self.client.logout()
        self.client.force_login(self.coach)
        page = self.client.get(self.url()).content.decode()
        self.assertIn("Орлов", page)  # черновик на месте после выхода
        # раскрыта одна пара: форма выбора маршрутов одна
        self.assertEqual(page.count('value="add_many"'), 1)
        resp = self.client.post(self.url(), {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk,
                                             f"sel_{self.p1.pk}": str(self.c_open.pk)})
        self.assertEqual(resp.status_code, 302)
        lines = ApplicationDraft.objects.get(user=self.coach).data["lines"]
        self.assertEqual([(r["a"], r["c"]) for r in lines], [(self.a2.pk, self.c_open.pk)])
        # недоступный зачёт: понятная ошибка с названием маршрута
        resp = self.client.post(self.url(), {"action": "add_many", "a": self.a2.pk, "h": self.h1.pk, f"sel_{self.p1.pk}": str(self.c_kids.pk)})
        self.assertContains(resp, "Маршрут №1, Дети")
        resp = self.client.post(self.url(), {"action": "submit", "key": "x"})
        app = self.coach.applications.get()
        self.assertFalse(ApplicationDraft.objects.filter(user=self.coach).exists())
        self.assertEqual(app.entries.count(), 1)

    def test_cabinet_shows_unfinished_draft(self):
        self.client.force_login(self.coach)
        self.client.post(self.url(), {"action": "pair_add", "athlete": self.a1.pk, "horse": self.h1.pk})
        self.assertContains(self.client.get(reverse("accounts:cabinet")), "Незавершённая заявка")

    def test_grouped_notifications(self):
        services.submit(self.coach, self.t.pk, [services.Line(self.a1, self.h1, self.c_kids), services.Line(self.a2, self.h1, self.c_open)], "k1")
        n = Notification.objects.filter(user=self.judge, kind="application")
        self.assertEqual(n.count(), 1)
        a3 = Athlete.objects.create(last_name="Третья", first_name="А", manager=self.coach, consent_confirmed_at=self.a1.consent_confirmed_at)
        services.submit(self.coach, self.t.pk, [services.Line(a3, self.h1, self.c_open)], "k4")
        n = Notification.objects.get(user=self.judge, kind="application")
        self.assertEqual(n.count, 2)
        self.assertIn("новых заявок — 2", n.text)
        entries = Entry.objects.filter(tournament=self.t)
        services.decide(self.judge, self.t.pk, [(e.pk, e.revision) for e in entries], "accept")
        decisions = Notification.objects.filter(user=self.coach, kind="decision")
        self.assertEqual(decisions.count(), 2)  # по одному на заявку, не на каждое выступление
        self.assertTrue(any("принято — 2" in d.text for d in decisions))


class RegistrationUxTests(Base):
    def test_athlete_profile_created_at_registration_and_phone_keyboard(self):
        page = self.client.get(reverse("accounts:register")).content.decode()
        self.assertIn('type="tel" name="phone"', page)
        self.assertNotIn('type="text" name="phone"', page)
        data = {"phone": "+79995550001", "password1": "Konkur-2026!", "password2": "Konkur-2026!", "last_name": "Белова", "first_name": "Ира",
                "categories": ["athlete"], "pd_consent": "on"}
        self.client.post(reverse("accounts:register"), data)  # тест 4: отдельного согласия на публикацию нет
        u = User.objects.get(phone="+79995550001")
        a = Athlete.objects.get(linked_user=u)
        self.assertEqual((a.last_name, a.manager_id), ("Белова", u.pk))
        self.assertIsNotNone(a.consent_confirmed_at)
        self.assertContains(self.client.get(reverse("accounts:cabinet")), "укажите дату рождения")


@override_settings(MEDIA_ROOT=TMP)
class DecideByFilterTests(Base):
    def test_accept_all_by_filter_through_confirm_page(self):
        services.submit(self.coach, self.t.pk, [services.Line(self.a1, self.h1, self.c_kids), services.Line(self.a2, self.h1, self.c_open)], "k1")
        self.client.force_login(self.judge)
        url = reverse("entries:decide", args=[self.t.pk])
        resp = self.client.post(url, {"action": "accept", "scope": "filter", "f_decision": "pending"})
        self.assertContains(resp, "Принять: 2")
        items = resp.context["items"]
        resp = self.client.post(url, {"action": "accept", "confirm": "1", "items": items})
        self.assertEqual(Entry.objects.filter(tournament=self.t, decision="accepted").count(), 2)
        # повтор того же подтверждения (двойное нажатие) — понятный отказ по каждой строке, без дублей
        resp = self.client.post(url, {"action": "accept", "confirm": "1", "items": items}, follow=True)
        self.assertContains(resp, "Не выполнено для 2")
