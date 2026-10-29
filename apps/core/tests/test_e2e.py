"""Сквозной сценарий (ТЗ v1, раздел 17.1, без онлайн-оплаты): от турнира до завершения."""

import datetime

from django.test import override_settings
from django.urls import reverse

from apps.conduct import services as cs
from apps.conduct.models import StartSlot
from apps.entries import services as es
from apps.entries.tests.test_entries import TMP, Base, mkuser
from apps.stables import services as ss
from apps.stables.models import Service, StableCategory
from apps.tournaments.models import TeamMember, Tournament


@override_settings(MEDIA_ROOT=TMP)
class EndToEndTests(Base):
    def test_full_tournament(self):
        t = self.t
        # публичная страница в календаре
        self.assertContains(self.client.get(reverse("core:calendar")), "Кубок")
        # команда турнира
        groom = mkuser("+79990003002")
        for u, role in ((groom, "stable"),):
            TeamMember.objects.create(tournament=t, phone=u.phone, user=u, role=role, state="accepted", invited_by=self.org)
        # денники и услуги
        cat = StableCategory.objects.create(tournament=t, name="Конюшня", available_from=self.start - datetime.timedelta(days=1),
                                            available_to=self.start + datetime.timedelta(days=1), capacity=5)
        sawdust = Service.objects.create(tournament=t, name="Опилки", unit="брикет")
        # групповая заявка тренера
        app = self.submit(self.coach, [es.Line(self.a1, self.h1, self.c_kids), es.Line(self.a2, self.h1, self.c_open)])
        e1, e2 = app.entries.order_by("pk")
        r = es.decide(self.judge, t.pk, [(e1.pk, e1.revision)], "accept")
        self.assertTrue(r[0].ok)
        e2.refresh_from_db()
        es.decide(self.judge, t.pk, [(e2.pk, e2.revision)], "reject", "лимит лошади")
        # бронь «без денника нельзя» и услуга
        b = ss.book(self.coach, cat.pk, self.h1, cat.available_from, cat.available_to, "e2e-b", required=True, contact_phone="+79990009999")
        o = ss.order(self.coach, sawdust.pk, 2, "e2e-o", horse=self.h1)
        cs.draw(self.judge, self.p1.pk)
        self.assertEqual(self.p1.slots.count(), 0)  # бронь не подтверждена — в протокол не попадает
        ss.decide(groom, b.pk, True)
        ss.assign_box(groom, b.pk, "7")
        cs.draw(self.judge, self.p1.pk)
        self.assertEqual(self.p1.slots.count(), 1)
        cs.publish_start_list(self.judge, self.p1.pk)
        page = self.client.get(reverse("tournaments:public", args=[t.pk]) + "?tab=starts")
        self.assertContains(page, "Смирнова Лиза")
        for secret in (self.coach.phone, "+79990009999", "лимит лошади", self.a1.birth_date.strftime("%d.%m.%Y")):
            self.assertNotContains(page, secret)
        # проведение и результаты
        slot = self.p1.slots.get()
        cs.set_status(self.judge, slot.pk, StartSlot.Status.STARTED)
        cs.save_result(self.judge, e1.pk, {"penalties": "0"}, "1", "")
        cs.publish_results(self.judge, self.c_kids.pk)
        self.assertContains(self.client.get(reverse("tournaments:public", args=[t.pk]) + "?tab=results"), "Смирнова Лиза")
        # выдача и завершение
        ss.deliver(groom, o.pk, 2)
        self.client.force_login(self.org)
        ov = self.client.get(reverse("conduct:overview", args=[t.pk]))
        self.client.post(reverse("conduct:finish", args=[t.pk]), {"confirm": "1"})
        t.refresh_from_db()
        self.assertEqual(t.publication, Tournament.Publication.FINISHED)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("tournaments:public", args=[t.pk]) + "?tab=results").status_code, 200)
        self.assertEqual(self.client.get(reverse("conduct:results_xlsx", args=[t.pk, self.c_kids.pk])).status_code, 404)
        self.client.force_login(self.org)
        self.assertEqual(self.client.get(reverse("conduct:results_xlsx", args=[t.pk, self.c_kids.pk])).status_code, 200)
