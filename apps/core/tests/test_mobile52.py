"""Тест 5.2 (29.10.2026): без масштабирования, безопасная зона iPhone, нижнее меню, календарь и страница турнира."""
from django.test import override_settings
from django.urls import reverse

from apps.entries.tests.test_entries import TMP, Base


@override_settings(MEDIA_ROOT=TMP)
class Mobile52Tests(Base):
    def test_viewport_no_zoom_and_safe_area(self):
        r = self.client.get(reverse("core:calendar"))
        self.assertContains(r, "maximum-scale=1, user-scalable=no, viewport-fit=cover")

    def test_bottom_menu_same_markup_for_all_items(self):
        self.client.force_login(self.coach)
        html = self.client.get(reverse("core:calendar")).content.decode()
        nav = html[html.index('class="bnav"'):html.index("</nav>", html.index('class="bnav"'))]
        self.assertEqual(nav.count('class="bnav__item'), 4)
        self.assertEqual(nav.count('class="bnav__ico"'), 4)
        self.assertEqual(nav.count('class="bnav__lbl"'), 4)

    def test_calendar_without_subtitle(self):
        self.assertNotContains(self.client.get(reverse("core:calendar")), "Опубликованные турниры, приём заявок")

    def test_tournament_page_without_published_line(self):
        for tab in ("", "?tab=starts", "?tab=results"):
            r = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + tab)
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, "Опубликовано ")
