"""Экраны «Окно соревнования» (R2_01) и «Стартовый протокол» с окном пары (R2_02A), 28.10.2026."""

from django.test import override_settings
from django.urls import reverse

from apps.conduct import services
from apps.conduct.tests.test_conduct import ConductBase
from apps.entries.tests.test_entries import TMP


@override_settings(MEDIA_ROOT=TMP)
class PublicWindowTests(ConductBase):
    def url(self, qs=""):
        return reverse("tournaments:public", args=[self.t.pk]) + qs

    def test_guest_sees_card_program_and_apply_bar(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        page = r.content.decode()
        for text in ("tp-tiles", "Даты", "Место", "Организатор", "Программа", "Стартовые", "Результаты"):
            self.assertIn(text, page)
        if self.t.public_entries_open():
            self.assertIn("tp-bar", page)
            self.assertIn(reverse("accounts:login") + "?next=" + reverse("entries:apply", args=[self.t.pk]), page)
        # строка программы ведёт к протоколу своего маршрута
        self.assertIn(f"?tab=starts&amp;prog={self.p1.pk}", page)
        # телефон контакта — только вошедшим (решение 26.10.2026)
        self.assertNotIn("tel:", page)
        self.assertNotIn(self.coach.phone, page)

    def test_starts_tab_shows_short_header_and_selected_route(self):
        services.draw(self.judge, self.p1.pk)
        services.publish_start_list(self.judge, self.p1.pk)
        r = self.client.get(self.url(f"?tab=starts&prog={self.p1.pk}"))
        self.assertContains(r, "tp-mini")
        self.assertContains(r, f'id="prog-{self.p1.pk}"')
        self.assertContains(r, "версия 1")
        self.assertNotContains(r, "tp-tiles")

    def test_pair_window_only_public_fields_from_snapshot(self):
        self.h1.owner_name = "Владелец Скрытый"
        self.h1.fksr_number = "123456"
        self.h1.save()
        self.a1.middle_name = "Отчествовна"
        self.a1.fksr_number = "654321"
        self.a1.save()
        services.draw(self.judge, self.p1.pk)
        services.publish_start_list(self.judge, self.p1.pk)
        # после публикации профиль изменился — публично видна опубликованная версия
        self.a1.club = "Клуб после публикации"
        self.a1.save()
        page = self.client.get(self.url(f"?tab=starts&prog={self.p1.pk}")).content.decode()
        self.assertIn('data-pair="pair-', page)
        self.assertIn("<template id=\"pair-", page)
        self.assertIn("Смирнова Лиза", page)
        self.assertIn(str(self.a1.birth_date.year), page)
        self.assertIn("Барон", page)
        for secret in (self.coach.phone, self.a1.birth_date.strftime("%d.%m.%Y"), self.a1.birth_date.isoformat(),
                       "Владелец Скрытый", "Отчествовна", "123456", "654321", self.h1.code, "Клуб после публикации"):
            self.assertNotIn(secret, page)

    def test_unpublished_start_list_hidden_from_guest(self):
        services.draw(self.judge, self.p1.pk)
        r = self.client.get(self.url(f"?tab=starts&prog={self.p1.pk}"))
        self.assertContains(r, "ещё не опубликован")
        self.assertNotContains(r, "Смирнова Лиза")
        self.assertNotContains(r, "data-pair=")

    def test_logged_in_sees_contact_phone_and_staff_panel(self):
        self.client.force_login(self.judge)
        r = self.client.get(self.url())
        self.assertContains(r, "tel:")
        self.assertContains(r, "Панель турнира")
