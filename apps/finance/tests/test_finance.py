import datetime

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.entries import services as eservices
from apps.entries.models import Entry
from apps.entries.tests.test_entries import TMP, Base, mkuser
from apps.finance import services
from apps.finance.models import Charge, Payment, Refund
from apps.tournaments import services as tservices
from apps.tournaments.models import TeamMember


@override_settings(MEDIA_ROOT=TMP)
class FinanceBase(Base):
    def setUp(self):
        super().setUp()
        self.cashier = mkuser("+79990001010")
        TeamMember.objects.create(tournament=self.t, phone=self.cashier.phone, user=self.cashier, role="cashier", state="accepted", invited_by=self.org)
        app = self.submit(self.coach, [eservices.Line(self.a1, self.h1, self.c_kids), eservices.Line(self.a2, self.h1, self.c_open)])
        self.e1, self.e2 = app.entries.order_by("pk")
        self.ch1 = Charge.objects.get(entry=self.e1)
        self.ch2 = Charge.objects.get(entry=self.e2)
        self.today = timezone.localdate()

    def pay(self, amount, charges, key="p1", user=None, method="cash"):
        return services.record_payment(user or self.cashier, self.t, self.coach.pk, amount, method, self.today, [c.pk for c in charges], key)

    def fresh(self, c):
        return Charge.objects.get(pk=c.pk)


class ChargeTests(FinanceBase):
    def test_charges_created_per_entry(self):
        self.assertEqual(self.ch1.amount, 150000)
        self.assertEqual(self.ch2.amount, 250000)
        self.assertEqual(self.ch1.payer, self.coach)
        self.assertEqual(self.ch1.direction, "sport")

    def test_repeat_submit_no_duplicate_charges(self):
        before = Charge.objects.count()
        lines = [eservices.Line(self.a2, self.h1, self.c_kids)]
        try:
            self.submit(self.coach, lines, key="same")
            self.submit(self.coach, lines, key="same")
        except ValueError:
            pass
        self.assertLessEqual(Charge.objects.count(), before + 1)

    def test_parse_rub(self):
        self.assertEqual(services.parse_rub("1 500"), 150000)
        self.assertEqual(services.parse_rub("1500,5"), 150050)
        self.assertEqual(services.parse_rub("1500.05"), 150005)
        for bad in ("", "abc", "1,234", "1.2.3"):
            with self.assertRaises(ValueError):
                services.parse_rub(bad)


class PaymentTests(FinanceBase):
    def test_partial_payment_allocated_in_order(self):
        self.pay(200000, [self.ch1, self.ch2])
        self.assertEqual(self.fresh(self.ch1).paid, 150000)
        self.assertEqual(self.fresh(self.ch1).balance, 0)
        self.assertEqual(self.fresh(self.ch2).paid, 50000)
        self.assertEqual(self.fresh(self.ch2).balance, 200000)

    def test_overpayment_rejected(self):
        with self.assertRaises(ValueError):
            self.pay(150001, [self.ch1])

    def test_repeat_key_no_duplicate(self):
        a = self.pay(100000, [self.ch1], key="same")
        b = self.pay(100000, [self.ch1], key="same")
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Payment.objects.count(), 1)

    def test_void_payment(self):
        p = self.pay(150000, [self.ch1])
        with self.assertRaises(ValueError):
            services.void_payment(self.cashier, p.pk, "")
        services.void_payment(self.cashier, p.pk, "ошибка кассы")
        self.assertEqual(self.fresh(self.ch1).paid, 0)

    def test_view_flow_and_representative_sees(self):
        self.client.force_login(self.cashier)
        r = self.client.get(reverse("finance:payer", args=[self.t.pk, self.coach.pk]))
        self.assertContains(r, "Отметить оплату")
        self.assertNotContains(r, self.coach.phone)
        self.client.post(reverse("finance:action", args=[self.t.pk, "pay"]), {
            "payer": self.coach.pk, "key": "k-view", "amount": "1 000", "method": "transfer", "paid_on": self.today.isoformat(), "charges": [self.ch1.pk],
        })
        self.client.post(reverse("finance:action", args=[self.t.pk, "pay"]), {
            "payer": self.coach.pk, "key": "k-view", "amount": "1 000", "method": "transfer", "paid_on": self.today.isoformat(), "charges": [self.ch1.pk],
        })
        self.assertEqual(Payment.objects.get().amount_kopecks, 100000)
        self.client.force_login(self.coach)
        r = self.client.get(reverse("finance:my"))
        self.assertContains(r, "1 000 ₽")
        self.assertContains(r, "500 ₽")  # остаток по зачёту «Дети»
        r = self.client.get(reverse("entries:application", args=[self.e1.application_id]))
        self.assertContains(r, "Начисления")


class AdjustRefundTests(FinanceBase):
    def test_adjustment_and_refund_cycle(self):
        self.pay(150000, [self.ch1])
        services.adjust(self.cashier, self.ch1.pk, 100000, "скидка клубу")
        c = self.fresh(self.ch1)
        self.assertEqual((c.base_kopecks, c.amount, c.balance), (150000, 100000, -50000))
        with self.assertRaises(ValueError):
            services.create_refund(self.cashier, c.pk, 150001, "переплата")
        r = services.create_refund(self.cashier, c.pk, 50000, "переплата после скидки")
        self.assertEqual(self.fresh(c).refundable, 100000)
        with self.assertRaises(ValueError):
            services.create_refund(self.cashier, c.pk, 100001, "ещё")
        services.complete_refund(self.cashier, r.pk, "cash", self.today)
        c = self.fresh(c)
        self.assertEqual((c.paid, c.refunded, c.balance), (150000, 50000, 0))
        # аннулировать оплату, из которой уже выдан возврат, нельзя
        with self.assertRaises(ValueError):
            services.void_payment(self.cashier, Payment.objects.get().pk, "ошибка")

    def test_fee_change_is_adjustment(self):
        self.pay(150000, [self.ch1])
        self.e1.refresh_from_db()
        p = eservices.propose(self.coach, self.e1.pk, tclass=self.c_open)
        eservices.decide_proposal(self.judge, p.pk, True)
        c = self.fresh(self.ch1)
        self.assertEqual(c.base_kopecks, 150000)
        self.assertEqual(c.amount, 250000)
        self.assertEqual(c.balance, 100000)

    def test_reject_writes_off_and_creates_refund_task(self):
        self.pay(150000, [self.ch1])
        self.e1.refresh_from_db()
        eservices.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision)], "reject", "лимит")
        c = self.fresh(self.ch1)
        self.assertTrue(c.source_cancelled)
        self.assertEqual(c.amount, 0)
        self.assertEqual(c.refund_pending, 150000)
        self.assertEqual(c.refunded, 0)  # задача, не факт
        self.e1.refresh_from_db()
        eservices.decide(self.judge, self.t.pk, [(self.e1.pk, self.e1.revision)], "accept")
        c = self.fresh(self.ch1)
        self.assertFalse(c.source_cancelled)
        self.assertEqual(c.amount, 150000)

    def test_cancel_by_representative_left_to_cashier(self):
        self.e2.refresh_from_db()
        eservices.cancel_by_representative(self.coach, self.e2.application_id, [(self.e2.pk, self.e2.revision)])
        c = self.fresh(self.ch2)
        self.assertTrue(c.source_cancelled)
        self.assertEqual(c.amount, 250000)
        self.assertIn("Отменённые позиции без решения", " ".join(services.finish_warnings(self.t)))
        self.client.force_login(self.cashier)
        self.client.post(reverse("finance:action", args=[self.t.pk, "write_off"]), {"charge": c.pk, "reason": "по условиям отмены"})
        self.assertEqual(self.fresh(c).amount, 0)

    def test_tournament_cancel_creates_refund_tasks(self):
        self.pay(100000, [self.ch1])
        self.t.refresh_from_db()
        tservices.cancel(self.org, self.t.pk, self.t.revision, "непогода")
        c1, c2 = self.fresh(self.ch1), self.fresh(self.ch2)
        self.assertEqual((c1.amount, c2.amount), (0, 0))
        self.assertEqual(Refund.objects.filter(state="open").count(), 1)
        self.assertEqual(c1.refund_pending, 100000)


class PermissionTests(FinanceBase):
    def test_access(self):
        urls = [reverse("finance:cash", args=[self.t.pk]), reverse("finance:payer", args=[self.t.pk, self.coach.pk]),
                reverse("finance:refunds", args=[self.t.pk]), reverse("finance:export", args=[self.t.pk])]
        post = (reverse("finance:action", args=[self.t.pk, "pay"]), {"payer": self.coach.pk, "key": "x", "amount": "100", "method": "cash",
                                                                      "paid_on": self.today.isoformat(), "charges": [self.ch1.pk]})
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 302)
        for who in (self.coach, self.judge, self.other):
            self.client.force_login(who)
            for u in urls:
                self.assertEqual(self.client.get(u).status_code, 403, (who, u))
            self.assertEqual(self.client.post(*post).status_code, 403)
        self.assertFalse(Payment.objects.exists())
        for who in (self.cashier, self.org):
            self.client.force_login(who)
            for u in urls:
                self.assertEqual(self.client.get(u).status_code, 200, (who, u))

    def test_foreign_charge_in_other_tournament(self):
        other_t = self.make_tournament()
        TeamMember.objects.create(tournament=other_t, phone=self.cashier.phone, user=self.cashier, role="cashier", state="accepted", invited_by=self.org)
        with self.assertRaises(ValueError):
            services.record_payment(self.cashier, other_t, self.coach.pk, 100, "cash", self.today, [self.ch1.pk], "zz")
        self.client.force_login(self.cashier)
        r = self.client.post(reverse("finance:action", args=[other_t.pk, "refund"]), {"charge": self.ch1.pk, "amount": "1", "reason": "x"})
        self.assertFalse(Refund.objects.exists())

    def test_my_money_only_own(self):
        self.client.force_login(self.other)
        r = self.client.get(reverse("finance:my"))
        self.assertNotContains(r, self.ch1.title)
