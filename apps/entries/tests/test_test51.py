"""Тест 5.1 (28.10.2026): «Мои соревнования», экран заявки, отмена вместе с денниками и услугами, подача заявки."""

import datetime

from django.test import override_settings
from django.urls import reverse

from apps.entries import services
from apps.entries.models import Entry
from apps.entries.tests.test_entries import TMP, Base
from apps.entries.views import _norm
from apps.profiles.models import Horse
from apps.stables import services as sservices
from apps.stables.models import Booking, Service, ServiceOrder, StableCategory
from apps.tournaments.models import ProgramItem, TournamentClass


@override_settings(MEDIA_ROOT=TMP)
class T51Base(Base):
    def setUp(self):
        super().setUp()
        self.h3 = Horse.objects.create(name="Гранит", birth_year=2012, color="grey", sex="gelding", breed="орловская", manager=self.coach)
        # второй день турнира — отдельный маршрут
        self.t.ends_on = self.start + datetime.timedelta(days=1)
        self.t.save()
        self.p2 = ProgramItem.objects.create(tournament=self.t, day=self.start + datetime.timedelta(days=1), discipline="jumping",
                                             name="Маршрут №2", program_type="manual", result_fields=["penalties"], heights=[90])
        self.c2 = TournamentClass.objects.create(program=self.p2, name="Открытый")
        self.cat = StableCategory.objects.create(tournament=self.t, name="Летний", available_from=self.start - datetime.timedelta(days=1),
                                                 available_to=self.start + datetime.timedelta(days=2), capacity=5)
        self.hay = Service.objects.create(tournament=self.t, name="Сено", unit="тюк", recipient="horse")
        self.app = self.submit(self.coach, [services.Line(self.a1, self.h1, self.c_kids), services.Line(self.a2, self.h3, self.c_open),
                                            services.Line(self.a2, self.h3, self.c2)])
        self.e_baron = self.app.entries.get(horse=self.h1)
        self.b_baron = sservices.book(self.coach, self.cat.pk, self.h1, self.cat.available_from, self.cat.available_to, "bk1")
        self.b_granit = sservices.book(self.coach, self.cat.pk, self.h3, self.cat.available_from, self.cat.available_to, "bk2")
        self.o_baron = sservices.order(self.coach, self.hay.pk, 2, "o1", horse=self.h1)
        self.o_granit = sservices.order(self.coach, self.hay.pk, 3, "o2", horse=self.h3)


class MyCompetitionsTests(T51Base):
    url = reverse("entries:competitions")

    def test_card_grouped_by_day_with_services_per_horse(self):
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertEqual(page.count("data-mc-card data-title"), 1)  # одна карточка на турнир
        self.assertEqual(page.count("data-mc-day>"), 2)  # два дня соревнований
        self.assertIn("Выступлений: 3 · Услуг: 2 · Денников: 2", page)
        self.assertIn("Сено × 2 тюк", page)
        self.assertLess(page.index("<strong>Барон</strong>", page.index("Денники и услуги</h3>")), page.index("Сено × 2 тюк"))
        self.assertIn(reverse("stables:book", args=[self.t.pk]), page)
        self.assertIn(reverse("entries:application", args=[self.app.pk]), page)
        self.assertNotIn(f"№{self.app.pk}", page)
        # поиск и фильтр — на странице
        self.assertIn("data-mc-q", page)
        for label in ("Все", "На рассмотрении", "Принято", "Отклонено"):
            self.assertIn(f">{label}</button>", page)
        self.assertNotIn("?tab=apps", page)
        self.assertNotIn("Ваши заявки, выступления", page)

    def test_search_normalisation(self):
        self.assertEqual(_norm("  Пёстрый   ЁЖ "), "пестрый еж")

    def test_guest_and_stranger(self):
        self.assertIn("login", self.client.get(self.url)["Location"])
        self.client.force_login(self.other)
        page = self.client.get(self.url).content.decode()
        self.assertNotIn("data-mc-card data-title", page)
        self.assertNotIn("Сено", page)


class ApplicationScreenTests(T51Base):
    def test_by_day_without_history(self):
        self.client.force_login(self.coach)
        page = self.client.get(reverse("entries:application", args=[self.app.pk])).content.decode()
        self.assertEqual(page.count('class="app-day"'), 2)
        self.assertNotIn("История", page)
        self.assertIn(reverse("entries:competitions"), page)

    def test_guest_and_stranger(self):
        url = reverse("entries:application", args=[self.app.pk])
        self.assertIn("login", self.client.get(url)["Location"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)


class CancelWithStablesTests(T51Base):
    def url(self):
        return reverse("entries:cancel", args=[self.app.pk])

    def test_confirm_lists_only_horses_left_without_starts(self):
        self.client.force_login(self.coach)
        e = self.app.entries.filter(horse=self.h3, program=self.p1).get()  # у «Гранита» остаётся выступление во второй день
        r = self.client.post(self.url(), {"entry": [self.e_baron.pk, e.pk]})
        page = r.content.decode()
        self.assertIn("Отменить и это?", page)
        self.assertIn(f'name="booking" value="{self.b_baron.pk}" checked', page)
        self.assertIn(f'name="order" value="{self.o_baron.pk}" checked', page)
        self.assertNotIn(f'name="booking" value="{self.b_granit.pk}"', page)
        self.assertNotIn(f'name="order" value="{self.o_granit.pk}"', page)
        self.assertIn(">Отменить</button>", page)
        self.assertNotIn("Отменить 2", page)

    def test_cancel_chosen_bookings_and_orders_in_same_request(self):
        self.client.force_login(self.coach)
        data = {"confirm": "1", "entry": [self.e_baron.pk], f"rev-{self.e_baron.pk}": self.e_baron.revision,
                "booking": [self.b_baron.pk], "order": [self.o_baron.pk]}
        r = self.client.post(self.url(), data)
        self.assertRedirects(r, reverse("entries:application", args=[self.app.pk]), fetch_redirect_response=False)
        self.e_baron.refresh_from_db()
        self.assertEqual(self.e_baron.decision, Entry.Decision.CANCELLED)
        self.assertEqual(Booking.objects.get(pk=self.b_baron.pk).state, Booking.State.CANCELLED)
        self.assertEqual(ServiceOrder.objects.get(pk=self.o_baron.pk).state, ServiceOrder.State.CANCELLED)
        # повтор того же запроса ничего не ломает и не трогает другое
        self.client.post(self.url(), data)
        self.assertEqual(Booking.objects.get(pk=self.b_granit.pk).state, Booking.State.PENDING)
        self.assertEqual(ServiceOrder.objects.get(pk=self.o_granit.pk).state, ServiceOrder.State.ORDERED)

    def test_unchecked_kept_and_horse_with_starts_protected(self):
        self.client.force_login(self.coach)
        data = {"confirm": "1", "entry": [self.e_baron.pk], f"rev-{self.e_baron.pk}": self.e_baron.revision,
                "booking": [self.b_granit.pk], "order": [self.o_granit.pk]}  # подставлены брони лошади с выступлениями
        self.client.post(self.url(), data)
        self.assertEqual(Booking.objects.get(pk=self.b_baron.pk).state, Booking.State.PENDING)  # не отмечено — сохранилось
        self.assertEqual(Booking.objects.get(pk=self.b_granit.pk).state, Booking.State.PENDING)
        self.assertEqual(ServiceOrder.objects.get(pk=self.o_granit.pk).state, ServiceOrder.State.ORDERED)

    def test_foreign_booking_not_cancelled(self):
        h = Horse.objects.create(name="Чужой", birth_year=2010, color="bay", sex="mare", breed="x", manager=self.other)
        foreign = sservices.book(self.other, self.cat.pk, h, self.cat.available_from, self.cat.available_to, "bk3")
        self.client.force_login(self.coach)
        self.client.post(self.url(), {"confirm": "1", "entry": [self.e_baron.pk], f"rev-{self.e_baron.pk}": self.e_baron.revision,
                                      "booking": [foreign.pk]})
        self.assertEqual(Booking.objects.get(pk=foreign.pk).state, Booking.State.PENDING)

    def test_guest_stranger_and_get(self):
        self.assertIn("login", self.client.post(self.url(), {"entry": [self.e_baron.pk]})["Location"])
        self.assertIn("login", self.client.get(self.url())["Location"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url(), {"entry": [self.e_baron.pk]}).status_code, 403)
        self.assertEqual(self.client.post(self.url(), {"confirm": "1", "entry": [self.e_baron.pk], "booking": [self.b_baron.pk]}).status_code, 403)
        self.assertEqual(Booking.objects.get(pk=self.b_baron.pk).state, Booking.State.PENDING)
        # «Назад» / обновление экрана подтверждения: GET не даёт пустой ответ 405 (белый экран)
        self.client.force_login(self.coach)
        self.assertRedirects(self.client.get(self.url()), reverse("entries:application", args=[self.app.pk]), fetch_redirect_response=False)

    def test_decide_get_returns_to_list(self):
        url = reverse("entries:decide", args=[self.t.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.judge)
        self.assertRedirects(self.client.get(url), reverse("entries:manage", args=[self.t.pk]), fetch_redirect_response=False)


class ApplyScreenTests(T51Base):
    def test_comment_behind_link_and_no_public_note(self):
        self.client.force_login(self.coach)
        page = self.client.get(reverse("entries:apply", args=[self.t.pk])).content.decode()
        self.assertIn("+ Добавить комментарий", page)
        self.assertIn('name="comment"', page)
        self.assertNotIn("Публично в протоколах", page)
