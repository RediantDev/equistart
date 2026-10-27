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
        self.sawdust = Service.objects.create(tournament=self.t, name="Опилки", unit="мешок", limit=5)
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
        self.client.force_login(self.coach)
        data = {"category": self.cat.pk, "horse": self.h1.pk, "arrive": self.d0.isoformat(), "depart": self.d1.isoformat(), "key": "kk"}
        r = self.client.post(reverse("stables:book", args=[self.t.pk]), data)
        self.assertContains(r, "ПРОВЕРЬТЕ БРОНЬ")
        self.assertFalse(Booking.objects.exists())
        r = self.client.post(reverse("stables:book", args=[self.t.pk]), {**data, "confirm": "1"})
        self.assertRedirects(r, reverse("stables:book", args=[self.t.pk]) + "#mine", fetch_redirect_response=False)
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
        self.assertIn('id="mine"', page)
        self.assertIn("Ожидает подтверждения", page)

    def test_view_overflow_shows_error_and_creates_nothing(self):
        self.client.force_login(self.coach)
        r = self.client.post(self.url, {**self.data, "horse": [self.h1.pk, self.h3.pk, self.h4.pk]})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "выбрано лошадей: 3")
        self.assertContains(r, "field--error")
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
        self.client.force_login(self.coach)
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
        self.sawdust = Service.objects.create(tournament=self.t, name="Опилки", unit="мешок", limit=5)
        self.hay = Service.objects.create(tournament=self.t, name="Сено", unit="тюк")
        self.power = Service.objects.create(tournament=self.t, name="Электричество", unit="сутки", recipient="trailer")
        self.url = reverse("stables:order", args=[self.t.pk])

    def post(self, **kw):
        return self.client.post(self.url, {"key": "cart1", **kw})

    def test_cart_creates_all_lines_and_returns_to_screen(self):
        self.client.force_login(self.coach)
        r = self.post(**{f"q-{self.sawdust.pk}-{self.h1.pk}": "2", f"q-{self.sawdust.pk}-{self.h3.pk}": "1",
                         f"q-{self.hay.pk}-{self.h1.pk}": "3", f"q-{self.power.pk}": "2", f"trailer-{self.power.pk}": "А123ВС"})
        self.assertRedirects(r, reverse("stables:book", args=[self.t.pk]) + "#mine", fetch_redirect_response=False)
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
