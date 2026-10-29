import datetime
import threading

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.conduct import services as cs
from apps.entries import services as eservices
from apps.entries.tests.test_entries import TMP, Base, mkuser
from apps.profiles.models import Horse
from apps.stables import services
from apps.stables.models import Booking, Service, ServiceOrder, StableCategory
from apps.tournaments.models import TeamMember


@override_settings(MEDIA_ROOT=TMP)
class StableBase(Base):
    def setUp(self):
        super().setUp()
        self.groom = mkuser("+79990001020")
        TeamMember.objects.create(tournament=self.t, phone=self.groom.phone, user=self.groom, role="stable", state="accepted", invited_by=self.org)
        self.d0 = self.start - datetime.timedelta(days=1)
        self.d1 = self.start + datetime.timedelta(days=1)
        self.cat = StableCategory.objects.create(tournament=self.t, name="Летний", available_from=self.d0, available_to=self.d1, capacity=1)
        self.h3 = Horse.objects.create(name="Гранит", birth_year=2012, color="grey", sex="gelding", breed="орловская", manager=self.coach)

    def book(self, horse, key, user=None, **kw):
        return services.book(user or self.coach, self.cat.pk, horse, self.d0, self.d1, key, **kw)


class BookingTests(StableBase):
    def test_capacity(self):
        b = self.book(self.h1, "b1")
        with self.assertRaises(services.Conflict):
            self.book(self.h3, "b2")
        self.assertEqual(self.book(self.h1, "b1").pk, b.pk)  # повтор — та же бронь
        self.assertEqual(Booking.objects.count(), 1)

    def test_period_rules(self):
        with self.assertRaises(ValueError):
            services.book(self.coach, self.cat.pk, self.h1, self.d1, self.d0, "x")
        with self.assertRaises(ValueError):
            services.book(self.coach, self.cat.pk, self.h1, self.d0 - datetime.timedelta(days=1), self.d1, "y")

    def test_foreign_horse(self):
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.book(self.h2, "z")  # лошадь другого представителя

    def test_confirm_creates_charge_and_box_clash(self):
        self.cat.capacity = 2
        self.cat.save()
        b1, b2 = self.book(self.h1, "b1"), self.book(self.h3, "b2")
        services.decide(self.groom, b1.pk, True)
        services.decide(self.groom, b2.pk, True)
        services.assign_box(self.groom, b1.pk, "a-1")
        with self.assertRaises(services.Conflict):
            services.assign_box(self.groom, b2.pk, "A-1")
        services.assign_box(self.groom, b2.pk, "A-2")

    def test_cannot_shrink_capacity_below_bookings(self):
        self.book(self.h1, "b1")
        data = {f: getattr(self.cat, f) for f in ["name", "available_from", "available_to", "capacity", "checkin_time", "checkout_time", "conditions", "is_active"]}
        data["capacity"] = 0
        with self.assertRaises(ValueError):
            services.save_category(self.groom, self.t, data, self.cat)

    def test_reject_needs_reason_and_frees_place(self):
        b = self.book(self.h1, "b1")
        with self.assertRaises(ValueError):
            services.decide(self.groom, b.pk, False, "")
        services.decide(self.groom, b.pk, False, "нет мест для жеребцов")
        self.book(self.h3, "b2")

    def test_required_booking_blocks_only_that_horse(self):
        app = self.submit(self.coach, [eservices.Line(self.a1, self.h1, self.c_kids), eservices.Line(self.a2, self.h3, self.c_open)])
        e1, e2 = app.entries.order_by("pk")
        eservices.decide(self.judge, self.t.pk, [(e1.pk, e1.revision), (e2.pk, e2.revision)], "accept")
        b = self.book(self.h1, "b1", required=True)
        cs.draw(self.judge, self.p1.pk)
        ids = set(self.p1.slots.values_list("entry_id", flat=True))
        self.assertEqual(ids, {e2.pk})
        services.decide(self.groom, b.pk, True)
        cs.add_missing(self.judge, self.p1.pk)
        self.assertEqual(set(self.p1.slots.values_list("entry_id", flat=True)), {e1.pk, e2.pk})

    def test_cancel_confirmed_flags_charge(self):
        b = self.book(self.h1, "b1")
        services.decide(self.groom, b.pk, True)
        services.cancel_booking(self.coach, b.pk)
        self.book(self.h3, "b2")  # место освободилось


class ServiceTests(StableBase):
    def setUp(self):
        super().setUp()
        self.sawdust = Service.objects.create(tournament=self.t, name="Опилки", unit="брикет", limit=5)
        self.power = Service.objects.create(tournament=self.t, name="Электричество", unit="сутки", recipient="trailer")

    def test_order_limit_and_partial_delivery(self):
        o = services.order(self.coach, self.sawdust.pk, 3, "o1", horse=self.h1)
        with self.assertRaises(services.Conflict):
            services.order(self.coach, self.sawdust.pk, 3, "o2", horse=self.h1)
        services.deliver(self.groom, o.pk, 2)
        with self.assertRaises(ValueError):
            services.deliver(self.groom, o.pk, 2)
        services.deliver(self.groom, o.pk, 1)
        o.refresh_from_db()
        self.assertEqual(o.delivery_state, "Выдано")
        with self.assertRaises(ValueError):
            services.cancel_order(self.coach, o.pk)

    def test_trailer_requires_number_and_cancel_writes_off(self):
        with self.assertRaises(ValueError):
            services.order(self.coach, self.power.pk, 1, "t1")
        o = services.order(self.coach, self.power.pk, 2, "t2", trailer="А123ВС")
        services.cancel_order(self.coach, o.pk)


class ViewTests(StableBase):
    def test_book_flow_confirm_before_order(self):
        """Прежняя двухшаговая форма (её использует конюшня) по-прежнему проверяет бронь до создания."""
        self.client.force_login(self.coach)
        data = {"category": self.cat.pk, "horse": self.h1.pk, "arrive": self.d0.isoformat(), "depart": self.d1.isoformat(), "key": "kk"}
        r = self.client.post(reverse("stables:book", args=[self.t.pk]), data)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Booking.objects.exists())
        r = self.client.post(reverse("stables:book", args=[self.t.pk]), {**data, "confirm": "1"})
        self.assertRedirects(r, reverse("stables:book", args=[self.t.pk]), fetch_redirect_response=False)  # тест 5.1: без перехода к якорю
        self.client.post(reverse("stables:book", args=[self.t.pk]), {**data, "confirm": "1"})
        self.assertEqual(Booking.objects.count(), 1)

    def test_staff_access(self):
        b = self.book(self.h1, "b1")
        urls = [reverse("stables:manage", args=[self.t.pk]) + f"?tab={x}" for x in ("bookings", "grid", "orders", "setup")] + [
            reverse("stables:category_new", args=[self.t.pk]), reverse("stables:service_new", args=[self.t.pk]), reverse("stables:category_edit", args=[self.t.pk, self.cat.pk])]
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 302)
        for who in (self.coach, self.judge, self.other):
            self.client.force_login(who)
            for u in urls:
                self.assertEqual(self.client.get(u).status_code, 403, (who, u))
            self.assertEqual(self.client.post(reverse("stables:action", args=[self.t.pk, "confirm"]), {"booking": b.pk}).status_code, 403)
        b.refresh_from_db()
        self.assertEqual(b.state, "pending")
        for who in (self.groom, self.org):
            self.client.force_login(who)
            for u in urls:
                self.assertEqual(self.client.get(u).status_code, 200, (who, u))
        r = self.client.get(reverse("stables:manage", args=[self.t.pk]))
        self.assertContains(r, "Барон")

    def test_stranger_cannot_cancel_booking(self):
        b = self.book(self.h1, "b1")
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("stables:my_cancel", args=["booking", b.pk])).status_code, 403)
        b.refresh_from_db()
        self.assertEqual(b.state, "pending")

    def test_booking_in_other_tournament_via_action(self):
        other_t = self.make_tournament()
        TeamMember.objects.create(tournament=other_t, phone=self.groom.phone, user=self.groom, role="stable", state="accepted", invited_by=self.org)
        b = self.book(self.h1, "b1")
        self.client.force_login(self.groom)
        self.assertEqual(self.client.post(reverse("stables:action", args=[other_t.pk, "confirm"]), {"booking": b.pk}).status_code, 404)


@override_settings(MEDIA_ROOT=TMP)
class LastBoxRaceTests(TransactionTestCase):
    """Параллельная бронь последнего денника даёт одну бронь (критерий этапа 5)."""

    def test_parallel_last_box(self):
        base = Base()
        base._testMethodName = "x"
        Base.setUp(base)
        start = base.start
        cat = StableCategory.objects.create(tournament=base.t, name="Последний", available_from=start, available_to=start + datetime.timedelta(days=1), capacity=1)
        horses = [Horse.objects.create(name=f"Л{i}", birth_year=2010, color="bay", sex="mare", breed="x", manager=base.coach) for i in range(4)]
        barrier = threading.Barrier(4)
        results = []

        def go(i):
            try:
                barrier.wait()
                services.book(base.coach, cat.pk, horses[i], start, start + datetime.timedelta(days=1), f"race-{i}")
                results.append("ok")
            except services.Conflict:
                results.append("conflict")
            except Exception as e:  # noqa
                results.append(repr(e))
            finally:
                connection.close()

        threads = [threading.Thread(target=go, args=(i,)) for i in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(sorted(results), ["conflict", "conflict", "conflict", "ok"])
        self.assertEqual(Booking.objects.filter(category=cat).count(), 1)


class StaffScreenTests(StableBase):
    def test_delivery_form_offers_remaining_and_default_tab_shows_nobox(self):
        from django.urls import reverse
        from apps.stables import services as S
        from apps.stables.models import Booking, ServiceOrder

        svc = self.t.services.create(name="Опилки", unit="тюк", recipient="horse", available_from=self.d0, available_to=self.d1)
        o = S.order(self.coach, svc.pk, 4, "k-s1", horse=self.h1)
        S.deliver(self.groom, o.pk, 3)
        self.client.force_login(self.groom)
        page = self.client.get(reverse("stables:manage", args=[self.t.pk]) + "?tab=orders").content.decode()
        self.assertIn('max="1" value="1"', page)  # остаток, а не весь заказ
        b = S.book(self.coach, self.cat.pk, self.h1, self.cat.available_from, self.cat.available_to, "k-b1")
        S.decide(self.groom, b.pk, True)
        page = self.client.get(reverse("stables:manage", args=[self.t.pk]) + "?tab=bookings").content.decode()
        self.assertIn("Назначить денник", page)  # без ожидающих — сразу брони без номера


class MultiHorseBookingTests(StableBase):
    """Бронь на несколько лошадей одной отправкой: всё или ничего, повтор — без дублей."""

    def setUp(self):
        super().setUp()
        self.cat.capacity = 2
        self.cat.save()
        self.h4 = Horse.objects.create(name="Вихрь", birth_year=2015, color="bay", sex="mare", breed="x", manager=self.coach)
        self.url = reverse("stables:book", args=[self.t.pk])
        self.data = {"category": self.cat.pk, "arrive": self.d0.isoformat(), "depart": self.d1.isoformat(), "key": "multi1", "confirm": "1"}

    def test_service_success_and_repeat(self):
        made = services.book_many(self.coach, self.cat.pk, [self.h1, self.h3], self.d0, self.d1, "m1")
        self.assertEqual({b.horse_id for b in made}, {self.h1.pk, self.h3.pk})
        again = services.book_many(self.coach, self.cat.pk, [self.h3, self.h1], self.d0, self.d1, "m1")
        self.assertEqual({b.pk for b in again}, {b.pk for b in made})
        self.assertEqual(Booking.objects.count(), 2)
        with self.assertRaises(services.Conflict):  # тот же ключ, другой состав
            services.book_many(self.coach, self.cat.pk, [self.h4], self.d0, self.d1, "m1")

    def test_capacity_overflow_all_or_nothing(self):
        with self.assertRaises(services.Conflict) as cm:
            services.book_many(self.coach, self.cat.pk, [self.h1, self.h3, self.h4], self.d0, self.d1, "m2")
        self.assertIn("Ни одна бронь не создана", str(cm.exception))
        self.assertEqual(Booking.objects.count(), 0)
        self.book(self.h1, "single")
        with self.assertRaises(services.Conflict):
            services.book_many(self.coach, self.cat.pk, [self.h3, self.h4], self.d0, self.d1, "m3")
        self.assertEqual(Booking.objects.count(), 1)

    def test_foreign_horse_in_batch(self):
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            services.book_many(self.coach, self.cat.pk, [self.h1, self.h2], self.d0, self.d1, "m4")
        self.assertEqual(Booking.objects.count(), 0)

    def test_view_multi_horse_and_repeat_submit(self):
        self.client.force_login(self.coach)
        r = self.client.post(self.url, {**self.data, "horse": [self.h1.pk, self.h3.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Booking.objects.count(), 2)
        self.client.post(self.url, {**self.data, "horse": [self.h1.pk, self.h3.pk]})  # двойное нажатие
        self.assertEqual(Booking.objects.count(), 2)
        page = self.client.get(self.url).content.decode()
        for b in Booking.objects.all():
            self.assertIn(f'id="b{b.pk}"', page)  # брони видны в карточках лошадей
        self.assertIn("Ожидает подтверждения", page)

    def test_view_overflow_shows_error_and_creates_nothing(self):
        self.client.force_login(self.coach)
        r = self.client.post(self.url, {**self.data, "horse": [self.h1.pk, self.h3.pk, self.h4.pk]})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "выбрано лошадей: 3")
        self.assertContains(r, "alert--error")
        self.assertEqual(Booking.objects.count(), 0)

    def test_view_bad_dates_marked_on_field(self):
        self.client.force_login(self.coach)
        r = self.client.post(self.url, {**self.data, "horse": [self.h1.pk], "arrive": self.d1.isoformat(), "depart": self.d0.isoformat()})
        self.assertContains(r, "Выезд должен быть позже заезда")
        self.assertEqual(Booking.objects.count(), 0)

    def test_view_foreign_horse_forbidden(self):
        self.client.force_login(self.coach)
        r = self.client.post(self.url, {**self.data, "horse": [self.h1.pk, self.h2.pk]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Booking.objects.count(), 0)

    def test_guest_redirected(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {**self.data, "horse": [self.h1.pk]}).status_code, 302)
        self.assertEqual(Booking.objects.count(), 0)

    def test_staff_books_several_including_new_horse_once(self):
        self.cat.capacity = 3
        self.cat.save()
        self.client.force_login(self.groom)
        self.assertContains(self.client.get(self.url), 'name="add_new"')
        self.assertEqual(self.client.post(self.url, {**self.data, "horse": [self.h1.pk]}).status_code, 403)  # лошадь не из заявок турнира
        data = {**self.data, "add_new": "on", "new_horse": "Новичок"}
        self.client.post(self.url, data)
        self.client.post(self.url, data)  # повтор не создаёт вторую лошадь и бронь
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Horse.objects.filter(name="Новичок").count(), 1)

    def test_empty_category_option_label(self):
        self.client.force_login(self.groom)  # ручная бронь конюшни — прежняя форма
        self.assertContains(self.client.get(self.url), '<select id="b-cat" name="category" required><option value="">— выберите —</option>')


class EditBookingTests(StableBase):
    def setUp(self):
        super().setUp()
        self.cat2 = StableCategory.objects.create(tournament=self.t, name="Конюшня", available_from=self.d0, available_to=self.d1 + datetime.timedelta(days=1), capacity=1)
        self.b = self.book(self.h1, "e1")
        self.url = reverse("stables:my_edit", args=[self.b.pk])

    def test_owner_edits_pending(self):
        self.client.force_login(self.coach)
        d2 = self.d1 + datetime.timedelta(days=1)
        r = self.client.post(self.url, {"category": self.cat2.pk, "arrive": self.d0.isoformat(), "depart": d2.isoformat(), "revision": self.b.revision})
        self.assertEqual(r.status_code, 302)
        self.b.refresh_from_db()
        self.assertEqual((self.b.category_id, self.b.depart, self.b.state), (self.cat2.pk, d2, "pending"))
        self.book(self.h3, "e2")  # место в старой категории освободилось

    def test_edit_respects_capacity_and_period(self):
        services.book(self.coach, self.cat2.pk, self.h3, self.d0, self.d1, "e3")
        with self.assertRaises(services.Conflict):
            services.update_booking(self.coach, self.b.pk, self.cat2.pk, self.d0, self.d1)
        with self.assertRaises(ValueError):
            services.update_booking(self.coach, self.b.pk, self.cat.pk, self.d0, self.d1 + datetime.timedelta(days=1))
        self.b.refresh_from_db()
        self.assertEqual(self.b.category_id, self.cat.pk)

    def test_confirmed_cannot_be_edited(self):
        services.decide(self.groom, self.b.pk, True)
        with self.assertRaises(services.Conflict):
            services.update_booking(self.coach, self.b.pk, self.cat2.pk, self.d0, self.d1)

    def test_permissions(self):
        data = {"category": self.cat2.pk, "arrive": self.d0.isoformat(), "depart": self.d1.isoformat()}
        self.assertEqual(self.client.post(self.url, data).status_code, 302)  # гость → вход
        for who in (self.other, self.groom):  # чужой пользователь и даже конюшня не меняют бронь представителя
            self.client.force_login(who)
            self.assertEqual(self.client.post(self.url, data).status_code, 403)
        self.b.refresh_from_db()
        self.assertEqual(self.b.category_id, self.cat.pk)
        self.client.force_login(self.coach)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(reverse("stables:my_edit", args=[999999]), data).status_code, 404)

    def test_contact_shown_only_to_owner(self):
        url = reverse("stables:book", args=[self.t.pk])
        self.client.force_login(self.coach)
        self.assertContains(self.client.get(url), f"tel:{self.groom.phone}")
        self.client.force_login(self.other)
        page = self.client.get(url).content.decode()
        self.assertNotIn(self.groom.phone, page)
        self.assertNotIn("Барон", page)  # чужие брони не видны


class MultiServiceOrderTests(StableBase):
    def setUp(self):
        super().setUp()
        self.sawdust = Service.objects.create(tournament=self.t, name="Опилки", unit="брикет", limit=5)
        self.hay = Service.objects.create(tournament=self.t, name="Сено", unit="тюк")
        self.power = Service.objects.create(tournament=self.t, name="Электричество", unit="сутки", recipient="trailer")
        self.url = reverse("stables:order", args=[self.t.pk])

    def post(self, **kw):
        return self.client.post(self.url, {"key": "cart1", **kw})

    def test_cart_creates_all_lines_and_returns_to_screen(self):
        self.client.force_login(self.coach)
        r = self.post(**{f"q-{self.sawdust.pk}-{self.h1.pk}": "2", f"q-{self.sawdust.pk}-{self.h3.pk}": "1",
                         f"q-{self.hay.pk}-{self.h1.pk}": "3", f"q-{self.power.pk}": "2", f"trailer-{self.power.pk}": "А123ВС"})
        self.assertRedirects(r, reverse("stables:book", args=[self.t.pk]), fetch_redirect_response=False)  # тест 5.1: без перехода к якорю
        self.assertEqual(ServiceOrder.objects.count(), 4)
        self.assertEqual(sorted(ServiceOrder.objects.values_list("recipient_label", flat=True)), ["Барон", "Барон", "Гранит", "Коневоз А123ВС"])
        self.post(**{f"q-{self.sawdust.pk}-{self.h1.pk}": "2"})  # повтор той же формы
        self.assertEqual(ServiceOrder.objects.count(), 4)
        page = self.client.get(reverse("stables:book", args=[self.t.pk])).content.decode()
        self.assertIn("Коневоз А123ВС", page)

    def test_cart_all_or_nothing_on_limit(self):
        self.client.force_login(self.coach)
        r = self.post(**{f"q-{self.sawdust.pk}-{self.h1.pk}": "3", f"q-{self.sawdust.pk}-{self.h3.pk}": "3", f"q-{self.hay.pk}-{self.h1.pk}": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "осталось только 5")
        self.assertContains(r, 'value="3"')  # введённое не теряется
        self.assertEqual(ServiceOrder.objects.count(), 0)

    def test_trailer_without_number(self):
        self.client.force_login(self.coach)
        r = self.post(**{f"q-{self.power.pk}": "1"})
        self.assertContains(r, "Укажите номер коневоза")
        self.assertEqual(ServiceOrder.objects.count(), 0)

    def test_empty_cart(self):
        self.client.force_login(self.coach)
        self.assertContains(self.post(), "Укажите количество")

    def test_permissions(self):
        self.assertEqual(self.post(**{f"q-{self.hay.pk}-{self.h1.pk}": "1"}).status_code, 302)  # гость → вход
        self.assertEqual(ServiceOrder.objects.count(), 0)
        # чужая лошадь в форму не попадает: поле игнорируется, заказ не создаётся
        self.client.force_login(self.other)
        self.post(**{f"q-{self.hay.pk}-{self.h1.pk}": "1"})
        self.assertFalse(ServiceOrder.objects.filter(horse=self.h1).exists())
        with self.assertRaises(PermissionDenied):
            services.order_many(self.other, self.t.pk, [{"service": self.hay.pk, "quantity": 1, "horse": self.h1}], "x1")
        # чужой заказ не отменить
        o = services.order(self.coach, self.hay.pk, 1, "own", horse=self.h1)
        self.assertEqual(self.client.post(reverse("stables:my_cancel", args=["order", o.pk])).status_code, 403)

    def test_book_page_has_single_order_button_and_no_label_wrapped_select(self):
        self.client.force_login(self.coach)
        page = self.client.get(reverse("stables:book", args=[self.t.pk])).content.decode()
        self.assertEqual(page.count("data-order-submit>"), 1)
        self.assertNotIn('<label class="field" style', page)  # select в <label> открывал второй (системный) список


class MyPageTests(StableBase):
    def test_grouped_by_tournament_with_link(self):
        self.book(self.h1, "g1")
        other_t = self.make_tournament()
        self.client.force_login(self.coach)
        page = self.client.get(reverse("stables:my")).content.decode()
        self.assertIn(reverse("stables:book", args=[self.t.pk]), page)
        self.assertNotIn(reverse("stables:book", args=[other_t.pk]), page)
        self.assertIn("Ожидает подтверждения", page)
        self.assertNotIn("касса", page)
        self.client.force_login(self.other)
        self.assertNotIn("Барон", self.client.get(reverse("stables:my")).content.decode())
        self.client.logout()
        self.assertEqual(self.client.get(reverse("stables:my")).status_code, 302)

    def test_screens_open_at_top(self):
        """Тест 5.1: экран денников открывается сверху — без якоря «#mine» в ссылках и возвратах."""
        b = self.book(self.h1, "g1")
        self.client.force_login(self.coach)
        my = self.client.get(reverse("stables:my")).content.decode()
        self.assertNotIn("#mine", my)
        self.assertIn("Мои денники и услуги", my)
        self.assertNotIn("Брони и заказы по турнирам", my)
        page = self.client.get(reverse("stables:book", args=[self.t.pk])).content.decode()
        self.assertNotIn("#mine", page)
        r = self.client.post(reverse("stables:my_cancel", args=["booking", b.pk]), {"next": reverse("stables:book", args=[self.t.pk])})
        self.assertRedirects(r, reverse("stables:book", args=[self.t.pk]), fetch_redirect_response=False)


class CartScreenTests(StableBase):
    """Макет 10B: ночи по каждой лошади, услуги счётчиками, одна кнопка «Заказать»."""

    def setUp(self):
        super().setUp()
        self.cat.capacity = 2
        self.cat.save()
        self.sawdust = Service.objects.create(tournament=self.t, name="Опилки", unit="брикет", limit=10)
        self.url = reverse("stables:book", args=[self.t.pk])
        self.n0, self.n1 = self.d0, self.d0 + datetime.timedelta(days=1)

    def post(self, key="c1", **kw):
        return self.client.post(self.url, {"mode": "cart", "key": key, **kw})

    def test_page_has_strip_counters_and_bar(self):
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertIn("Отметьте ночи для каждой лошади", page)
        self.assertIn("Всем как у первой", page)
        self.assertIn(f'name="n-{self.h1.pk}" value="{self.n0.isoformat()}"', page)
        self.assertIn(f'name="q-{self.sawdust.pk}-{self.h3.pk}"', page)
        self.assertIn("без денника", page)
        self.assertEqual(page.count("data-order-submit>"), 1)
        self.assertNotIn("Ладога", page)  # чужая лошадь
        self.assertNotIn("₽", page)

    def test_several_horses_own_nights_and_services_once(self):
        self.client.force_login(self.coach)
        data = {f"n-{self.h1.pk}": [self.n0.isoformat(), self.n1.isoformat()], f"n-{self.h3.pk}": [self.n1.isoformat()],
                f"q-{self.sawdust.pk}-{self.h1.pk}": "2", f"q-{self.sawdust.pk}-{self.h3.pk}": "1"}
        r = self.post(**data)
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        b1, b3 = Booking.objects.get(horse=self.h1), Booking.objects.get(horse=self.h3)
        self.assertEqual((b1.arrive, b1.depart, b1.state), (self.n0, self.n1 + datetime.timedelta(days=1), "pending"))
        self.assertEqual((b3.arrive, b3.depart), (self.n1, self.n1 + datetime.timedelta(days=1)))
        self.assertEqual(ServiceOrder.objects.count(), 2)
        self.post(**data)  # повтор отправки
        self.assertEqual((Booking.objects.count(), ServiceOrder.objects.count()), (2, 2))
        page = self.client.get(self.url).content.decode()
        self.assertIn(f'id="b{b1.pk}"', page)
        self.assertIn("2 ночи", page)

    def test_capacity_all_or_nothing(self):
        self.cat.capacity = 1
        self.cat.save()
        self.client.force_login(self.coach)
        r = self.post(**{f"n-{self.h1.pk}": self.n0.isoformat(), f"n-{self.h3.pk}": self.n0.isoformat(), f"q-{self.sawdust.pk}-{self.h1.pk}": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ни одна бронь не создана")
        self.assertContains(r, f'value="{self.n0.isoformat()}" checked')  # выбор не теряется
        self.assertEqual((Booking.objects.count(), ServiceOrder.objects.count()), (0, 0))

    def test_service_limit_rolls_back_bookings(self):
        self.client.force_login(self.coach)
        r = self.post(**{f"n-{self.h1.pk}": self.n0.isoformat(), f"q-{self.sawdust.pk}-{self.h1.pk}": "11"})
        self.assertContains(r, "осталось только 10")
        self.assertEqual(Booking.objects.count(), 0)

    def test_gap_in_nights_rejected(self):
        self.cat.available_to = self.d0 + datetime.timedelta(days=3)
        self.cat.save()
        self.client.force_login(self.coach)
        r = self.post(**{f"n-{self.h1.pk}": [self.n0.isoformat(), (self.n0 + datetime.timedelta(days=2)).isoformat()]})
        self.assertContains(r, "ночи должны идти подряд")
        self.assertEqual(Booking.objects.count(), 0)

    def test_overlap_with_existing_booking(self):
        self.book(self.h1, "old")
        self.client.force_login(self.coach)
        r = self.post(**{f"n-{self.h1.pk}": self.n0.isoformat()})
        self.assertContains(r, "Уже есть бронь")
        self.assertEqual(Booking.objects.count(), 1)

    def test_foreign_horse_and_guest(self):
        self.client.force_login(self.coach)
        self.assertEqual(self.post(**{f"n-{self.h2.pk}": self.n0.isoformat()}).status_code, 403)
        self.assertEqual(self.post(**{f"q-{self.sawdust.pk}-{self.h2.pk}": "1"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.post(**{f"n-{self.h1.pk}": self.n0.isoformat()}).status_code, 302)
        self.assertEqual((Booking.objects.count(), ServiceOrder.objects.count()), (0, 0))
        with self.assertRaises(PermissionDenied):
            services.submit_cart(self.coach, self.t.pk, [{"category": self.cat.pk, "horse": self.h2, "arrive": self.d0, "depart": self.d1}], [], "z")

    def test_repeat_key_of_other_user_forbidden(self):
        self.client.force_login(self.coach)
        self.post(**{f"n-{self.h1.pk}": self.n0.isoformat()})
        with self.assertRaises(PermissionDenied):
            services.submit_cart(self.other, self.t.pk, [{"category": self.cat.pk, "horse": self.h2, "arrive": self.d0, "depart": self.d1}], [], "c1")

    def test_cancel_from_card(self):
        b = self.book(self.h1, "old")
        services.decide(self.groom, b.pk, True)
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertIn(f'form="x{b.pk}"', page)
        self.assertNotIn(f'form="e{b.pk}"', page)  # подтверждённую — только отменить
        r = self.client.post(reverse("stables:my_cancel", args=["booking", b.pk]), {"next": self.url})
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        b.refresh_from_db()
        self.assertEqual(b.state, "cancelled")

    def test_closed_tournament_no_order_button(self):
        self.t.publication = "finished"
        self.t.save()
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertIn("Бронирование закрыто", page)
        self.assertNotIn("data-order-submit", page)


class Test52Tests(StableBase):
    """Тест 5.2: отмена без причины (п. 26), понятные услуги (п. 29, 30), свёрнутый список и изменение ночей с ленты (п. 23, 25)."""

    def setUp(self):
        super().setUp()
        self.cat.capacity = 2
        self.cat.save()
        self.sawdust = Service.objects.create(tournament=self.t, name="Опилки", unit="брикет")
        self.power = Service.objects.create(tournament=self.t, name="Электричество для коневоза", unit="сутки", recipient="trailer")
        self.url = reverse("stables:book", args=[self.t.pk])

    def test_participant_cancels_booking_without_reason(self):
        b = self.book(self.h1, "b1")
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertNotIn(f'name="reason" form="x{b.pk}"', page)  # поля «причина» нет
        self.assertIn('data-confirm="Отменить бронь денника', page)
        r = self.client.post(reverse("stables:my_cancel", args=["booking", b.pk]), {"next": self.url})
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        b.refresh_from_db()
        self.assertEqual((b.state, b.reason), ("cancelled", "Отменено участником"))

    def test_participant_cancels_order_without_reason(self):
        o = services.order(self.coach, self.sawdust.pk, 2, "o1", horse=self.h1)
        self.client.force_login(self.coach)
        self.assertIn('data-confirm="Отменить заказ: Опилки', self.client.get(self.url).content.decode())
        self.client.post(reverse("stables:my_cancel", args=["order", o.pk]), {"next": self.url})
        o.refresh_from_db()
        self.assertEqual(o.state, "cancelled")
        from apps.core.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="service_order.cancel", data__reason="Отменено участником").exists())

    def test_foreign_and_guest_cancel(self):
        b = self.book(self.h1, "b1")
        o = services.order(self.coach, self.sawdust.pk, 1, "o1", horse=self.h1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("stables:my_cancel", args=["booking", b.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("stables:my_cancel", args=["order", o.pk])).status_code, 403)
        self.client.logout()
        for kind, pk in (("booking", b.pk), ("order", o.pk)):
            r = self.client.post(reverse("stables:my_cancel", args=[kind, pk]))
            self.assertEqual(r.status_code, 302)
            self.assertIn(reverse("accounts:login"), r["Location"])
        b.refresh_from_db(), o.refresh_from_db()
        self.assertEqual((b.state, o.state), ("pending", "ordered"))

    def test_staff_still_needs_reason_for_others_booking(self):
        b = self.book(self.h1, "b1")
        with self.assertRaises(ValueError):
            services.cancel_booking(self.groom, b.pk, "")
        services.cancel_booking(self.groom, b.pk, "Ветеринарный карантин")
        b.refresh_from_db()
        self.assertEqual(b.reason, "Ветеринарный карантин")

    def test_collapsed_summary_and_strip_edit_marks(self):
        b = self.book(self.h1, "b1")
        services.order(self.coach, self.sawdust.pk, 2, "o1", horse=self.h1)
        b2 = self.book(self.h3, "b2")
        services.decide(self.groom, b2.pk, True)
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertIn('<details class="sb-mine-box" data-mine>', page)  # по умолчанию свёрнут
        self.assertIn(" · ожидает · 1 услуга", page)
        self.assertIn(f'data-edit-bk="{b.pk}"', page)
        self.assertIn(f'data-bk="{b.pk}"', page)
        self.assertNotIn(f'data-edit-bk="{b2.pk}"', page)  # подтверждённую с ленты не меняем
        self.assertIn("· подтверждена", page)

    def test_others_service_full_name(self):
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertIn('<b class="sb-svc__name">Электричество для коневоза</b>', page)
        self.assertIn("Количество, сутки</label>", page)  # единица — из услуги
        self.assertIn("Госномер коневоза", page)
        self.assertIn('<h2 class="h3" id="st-h">Для конюшни</h2>', page)
        self.assertIn('M2 7h11v9H2z', page)  # значок грузовика у блока

    def test_strip_edit_shrinks_and_extends_pending(self):
        self.cat.available_to = self.d1 + datetime.timedelta(days=1)
        self.cat.save()
        b = self.book(self.h1, "b1")  # d0–d1, 2 ночи
        self.client.force_login(self.coach)
        edit = reverse("stables:my_edit", args=[b.pk])
        self.client.post(edit, {"category": self.cat.pk, "arrive": (self.d0 + datetime.timedelta(days=1)).isoformat(), "depart": self.d1.isoformat(), "revision": b.revision})
        b.refresh_from_db()
        self.assertEqual((b.arrive, b.depart, b.state), (self.d0 + datetime.timedelta(days=1), self.d1, "pending"))
        # добавить ночь, когда мест нет: бронь не меняется
        self.cat.capacity = 1
        self.cat.save()
        Booking.objects.create(tournament=self.t, category=self.cat, horse=self.h3, horse_name="Гранит", requested_by=self.coach,
                               arrive=self.d1, depart=self.d1 + datetime.timedelta(days=1), idempotency_key="x")
        self.client.post(edit, {"category": self.cat.pk, "arrive": b.arrive.isoformat(), "depart": (self.d1 + datetime.timedelta(days=1)).isoformat(), "revision": b.revision})
        b.refresh_from_db()
        self.assertEqual(b.depart, self.d1)

    def test_strip_edit_confirmed_foreign_and_guest(self):
        b = self.book(self.h1, "b1")
        edit = reverse("stables:my_edit", args=[b.pk])
        data = {"category": self.cat.pk, "arrive": (self.d0 + datetime.timedelta(days=1)).isoformat(), "depart": self.d1.isoformat()}
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(edit, data).status_code, 403)
        self.client.logout()
        r = self.client.post(edit, data)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])
        services.decide(self.groom, b.pk, True)
        self.client.force_login(self.coach)
        self.client.post(edit, data)
        b.refresh_from_db()
        self.assertEqual((b.arrive, b.state), (self.d0, "confirmed"))


class Test52VariantATests(StableBase):
    """Тест 5.2, вариант A: строки оформленного с меню «⋯», правка ночей с ленты — итоговым набором ночей через my_edit."""

    def setUp(self):
        super().setUp()
        self.cat.capacity = 2
        self.cat.available_to = self.d1 + datetime.timedelta(days=2)
        self.cat.save()
        self.hay = Service.objects.create(tournament=self.t, name="Сено", unit="тюк")
        self.url = reverse("stables:book", args=[self.t.pk])
        self.b = self.book(self.h1, "b1")  # d0–d1: 2 ночи
        self.edit = reverse("stables:my_edit", args=[self.b.pk])

    def day(self, n):
        return (self.d0 + datetime.timedelta(days=n)).isoformat()

    def test_rows_and_menu(self):
        services.order(self.coach, self.hay.pk, 2, "o1", horse=self.h1)
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertIn("<b>Денник</b>", page)
        self.assertIn("2 тюка", page)
        self.assertIn(f'data-edit-nights="{self.b.pk}"', page)
        self.assertIn(">Отменить бронь</button>", page)
        self.assertIn(">Отменить услугу</button>", page)
        self.assertIn("data-edit-panel", page)
        self.assertNotIn('name="reason"', page)

    def test_nights_shift_and_extend(self):
        self.client.force_login(self.coach)
        self.client.post(self.edit, {"nights": [self.day(1), self.day(2), self.day(3)], "revision": self.b.revision})
        self.b.refresh_from_db()
        self.assertEqual((self.b.arrive.isoformat(), self.b.depart.isoformat(), self.b.state, self.b.category_id), (self.day(1), self.day(4), "pending", self.cat.pk))

    def test_gap_rejected(self):
        self.client.force_login(self.coach)
        r = self.client.post(self.edit, {"nights": [self.day(0), self.day(2)], "revision": self.b.revision}, follow=True)
        self.assertContains(r, "должны идти подряд")
        self.b.refresh_from_db()
        self.assertEqual((self.b.arrive, self.b.depart, self.b.revision), (self.d0, self.d1, 1))

    def test_capacity_checked(self):
        self.cat.capacity = 1
        self.cat.save()
        Booking.objects.create(tournament=self.t, category=self.cat, horse=self.h3, horse_name="Гранит", requested_by=self.coach,
                               arrive=self.d1, depart=self.d1 + datetime.timedelta(days=1), idempotency_key="x")
        self.client.force_login(self.coach)
        self.client.post(self.edit, {"nights": [self.day(0), self.day(1), self.day(2)], "revision": self.b.revision})
        self.b.refresh_from_db()
        self.assertEqual(self.b.depart, self.d1)

    def test_stale_revision(self):
        self.client.force_login(self.coach)
        self.client.post(self.edit, {"nights": [self.day(1)], "revision": 99})
        self.b.refresh_from_db()
        self.assertEqual(self.b.arrive, self.d0)

    def test_confirmed_foreign_guest(self):
        data = {"nights": [self.day(1)]}
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.edit, data).status_code, 403)
        self.client.logout()
        r = self.client.post(self.edit, data)
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])
        services.decide(self.groom, self.b.pk, True)
        self.client.force_login(self.coach)
        page = self.client.get(self.url).content.decode()
        self.assertNotIn(f'data-edit-nights="{self.b.pk}"', page)
        r = self.client.post(self.edit, data, follow=True)
        self.assertContains(r, "Бронь уже рассмотрена")
        self.b.refresh_from_db()
        self.assertEqual((self.b.arrive, self.b.state), (self.d0, "confirmed"))


class Test53Tests(StableBase):
    """Тест 5.3: опилки — брикетами (п. 21); статус под строкой, «⋯» справа (п. 23, 24)."""

    def test_sawdust_migration_and_forms(self):
        from importlib import import_module

        from django.apps import apps as django_apps

        from apps.stables.views import qty_label

        a = Service.objects.create(tournament=self.t, name="Опилки древесные", unit="мешок")
        b = Service.objects.create(tournament=self.t, name="Сено", unit="мешок")
        import_module("apps.stables.migrations.0004_sawdust_briquette").sawdust_to_briquette(django_apps, None)
        a.refresh_from_db(), b.refresh_from_db()
        self.assertEqual((a.unit, b.unit), ("брикет", "мешок"))  # только опилки
        self.assertEqual([qty_label(n, "брикет") for n in (1, 2, 5, 11, 21)], ["1 брикет", "2 брикета", "5 брикетов", "11 брикетов", "21 брикет"])

    def test_row_layout_status_under_title(self):
        s = Service.objects.create(tournament=self.t, name="Опилки", unit="брикет")
        bk = self.book(self.h1, "b1")
        services.order(self.coach, s.pk, 2, "o1", horse=self.h1)
        self.client.force_login(self.coach)
        page = self.client.get(reverse("stables:book", args=[self.t.pk])).content.decode()
        self.assertIn("2 брикета", page)
        self.assertIn("Опилки<span class=\"sb-si__unit\">, брикет</span>", page)
        row = page[page.index(f'id="b{bk.pk}"'):]
        main_end = row.index("</div>")
        self.assertLess(row.index("Ожидает подтверждения"), main_end)  # статус внутри колонки названия
        self.assertGreater(row.index('class="sb-menu"'), main_end)  # «⋯» после неё, справа
        self.client.logout()
        r = self.client.get(reverse("stables:book", args=[self.t.pk]))
        self.assertEqual(r.status_code, 302)
