import datetime
import threading

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.conduct import services as cs
from apps.entries import services as eservices
from apps.entries.tests.test_entries import TMP, Base, mkuser
from apps.finance.models import Charge
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
        self.cat = StableCategory.objects.create(tournament=self.t, name="Летний", price_kopecks=300000, available_from=self.d0, available_to=self.d1, capacity=1)
        self.h3 = Horse.objects.create(name="Гранит", birth_year=2012, color="grey", sex="gelding", breed="орловская", manager=self.coach)

    def book(self, horse, key, user=None, **kw):
        return services.book(user or self.coach, self.cat.pk, horse, self.d0, self.d1, key, **kw)


class BookingTests(StableBase):
    def test_price_and_capacity(self):
        b = self.book(self.h1, "b1")
        self.assertEqual(b.amount_kopecks, 600000)
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
        ch = Charge.objects.get(source_key=f"booking:{b1.pk}")
        self.assertEqual((ch.amount, ch.direction, ch.payer), (600000, "stable", self.coach))
        services.assign_box(self.groom, b1.pk, "a-1")
        with self.assertRaises(services.Conflict):
            services.assign_box(self.groom, b2.pk, "A-1")
        services.assign_box(self.groom, b2.pk, "A-2")

    def test_cannot_shrink_capacity_below_bookings(self):
        self.book(self.h1, "b1")
        data = {f: getattr(self.cat, f) for f in ["name", "price_kopecks", "price_mode", "available_from", "available_to", "capacity", "checkin_time", "checkout_time", "conditions", "is_active"]}
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
        eservices.decide(self.judge, self.t.pk, [(e1.pk, e1.revision), (e2.pk, e2.revision)], "accept_admit")
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
        self.assertTrue(Charge.objects.get(source_key=f"booking:{b.pk}").source_cancelled)
        self.book(self.h3, "b2")  # место освободилось


class ServiceTests(StableBase):
    def setUp(self):
        super().setUp()
        self.sawdust = Service.objects.create(tournament=self.t, name="Опилки", unit="мешок", price_kopecks=50000, limit=5)
        self.power = Service.objects.create(tournament=self.t, name="Электричество", unit="сутки", price_kopecks=100000, recipient="trailer")

    def test_order_limit_and_partial_delivery(self):
        o = services.order(self.coach, self.sawdust.pk, 3, "o1", horse=self.h1)
        self.assertEqual(Charge.objects.get(source_key=f"order:{o.pk}").amount, 150000)
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
        self.assertEqual(Charge.objects.get(source_key=f"order:{o.pk}").amount, 0)


class ViewTests(StableBase):
    def test_book_flow_shows_sum_before_order(self):
        self.client.force_login(self.coach)
        data = {"category": self.cat.pk, "horse": self.h1.pk, "arrive": self.d0.isoformat(), "depart": self.d1.isoformat(), "key": "kk"}
        r = self.client.post(reverse("stables:book", args=[self.t.pk]), data)
        self.assertContains(r, "Забронировать за 6")
        self.assertFalse(Booking.objects.exists())
        r = self.client.post(reverse("stables:book", args=[self.t.pk]), {**data, "confirm": "1", "amount": "600000"})
        self.assertRedirects(r, reverse("stables:my"))
        self.client.post(reverse("stables:book", args=[self.t.pk]), {**data, "confirm": "1", "amount": "600000"})
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
        cat = StableCategory.objects.create(tournament=base.t, name="Последний", price_kopecks=100, available_from=start, available_to=start + datetime.timedelta(days=1), capacity=1)
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
