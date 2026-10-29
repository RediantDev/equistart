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


@override_settings(MEDIA_ROOT=TMP)
class CalendarB52Tests(Base):
    """Тест 5.2, вариант B: календарь-лента/месяц, карточки A, «Мои соревнования» по датам."""

    def test_calendar_renders_for_guest_and_user(self):
        for login in (None, self.coach):
            if login:
                self.client.force_login(login)
            r = self.client.get(reverse("core:calendar"))
            self.assertContains(r, 'data-cal-key="cal-main"')
            self.assertContains(r, 'id="cal-dates"')
            self.assertContains(r, 'class="tcard')
            self.assertNotContains(r, "Подробнее")
            self.assertNotContains(r, "Программ:")

    def test_dates_only_of_published_tournaments(self):
        import datetime

        from apps.tournaments.models import Tournament

        hidden_day = self.start + datetime.timedelta(days=3)
        Tournament.objects.create(owner=self.org, title="Черновик", starts_on=hidden_day, ends_on=hidden_day, venue=self.t.venue, timezone="Europe/Moscow")
        dates = self.client.get(reverse("core:calendar")).context["cal_dates"]
        self.assertEqual(dates.get(self.start.isoformat()), 1)
        self.assertNotIn(hidden_day.isoformat(), dates)

    def test_dates_follow_search(self):
        dates = self.client.get(reverse("core:calendar"), {"q": "нет такого турнира"}).context["cal_dates"]
        self.assertEqual(dates, {})

    def test_day_shows_tournaments_of_that_day(self):
        import datetime

        r = self.client.get(reverse("core:calendar"), {"day": self.start.isoformat()})
        self.assertContains(r, "cal-day-head")
        self.assertContains(r, reverse("tournaments:public", args=[self.t.pk]))
        r = self.client.get(reverse("core:calendar"), {"day": (self.start + datetime.timedelta(days=1)).isoformat()})
        self.assertContains(r, "В этот день соревнований нет")
        self.assertEqual(self.client.get(reverse("core:calendar"), {"day": "мусор"}).status_code, 200)

    def test_card_status_line(self):
        r = self.client.get(reverse("core:calendar"))
        self.assertContains(r, "tcard__st--")

    def test_my_competitions_only_own_dates(self):
        from apps.entries.models import Entry

        from apps.entries import services

        self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids)])
        self.assertTrue(Entry.objects.exists())
        self.client.force_login(self.coach)
        r = self.client.get(reverse("entries:competitions"))
        self.assertContains(r, 'data-cal-key="cal-mine"')
        self.assertIn(self.start.isoformat(), r.context["calendar_dates"]["upcoming"])
        # Чужие даты не утекают: у другого тренера заявок нет — календарь пуст
        self.client.force_login(self.other)
        r = self.client.get(reverse("entries:competitions"))
        self.assertEqual(r.context["calendar_dates"], {"upcoming": [], "past": []})
        self.assertNotContains(r, self.start.isoformat())

    def test_my_competitions_guest_redirected(self):
        r = self.client.get(reverse("entries:competitions"))
        self.assertEqual(r.status_code, 302)


@override_settings(MEDIA_ROOT=TMP)
class Test53Tests(Base):
    """Тест 5.3: поиск по мере ввода, «Все соревнования», клубы без подзаголовка, цветная карточка."""

    def test_calendar_live_search_and_all_label(self):
        r = self.client.get(reverse("core:calendar"), {"day": self.start.isoformat()})
        self.assertContains(r, 'data-live="#cal-list,#cal-dates,#cal-fbtn"')
        self.assertContains(r, "Все соревнования")
        self.assertNotContains(r, "Все даты")
        self.assertNotContains(r, "data-require-btn>Найти")

    def test_card_date_colored_by_state(self):
        r = self.client.get(reverse("core:calendar"))
        self.assertContains(r, "tcard__date--")
        self.assertContains(r, "tcard__disc")

    def test_clubs_without_subtitle_and_find_button(self):
        for login in (None, self.coach):
            if login:
                self.client.force_login(login)
            r = self.client.get(reverse("clubs:list"), {"q": "нет"})
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, "Проверенные клубы: адреса")
            self.assertNotContains(r, ">Найти</button>")
            self.assertContains(r, 'data-live="#club-results"')
            self.assertContains(r, 'id="club-results"')
