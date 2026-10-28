"""Доступ к лошади для заявок (решение 28.10.2026): даёт только управляющий, нужен ответ получателя."""
from django.test import override_settings
from django.urls import reverse

from apps.entries.tests.test_entries import TMP, Base, mkuser
from apps.profiles import services
from apps.profiles.models import HorseAccess, Offer


@override_settings(MEDIA_ROOT=TMP)
class HorseAccessTests(Base):
    def grant(self, phone="+79990001004"):
        self.client.force_login(self.coach)
        return self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "grant"]), {"phone": phone})

    def test_invite_accept_then_horse_available_for_entries(self):
        self.grant()
        o = Offer.objects.get(kind=Offer.Kind.HORSE_ACCESS, horse=self.h1)
        self.assertEqual(o.state, Offer.State.PENDING)
        self.assertNotIn(self.h1, list(services.horses_for_entry(self.other)))  # до согласия доступа нет
        self.client.force_login(self.other)
        self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "accept"})
        self.assertTrue(HorseAccess.objects.filter(horse=self.h1, user=self.other).exists())
        self.assertIn(self.h1, list(services.horses_for_entry(self.other)))
        r = self.client.get(reverse("profiles:horses"))
        self.assertContains(r, "Барон")
        self.assertContains(r, "Доступ")
        # без редактирования и без документов
        self.assertFalse(services.can_manage_horse(self.other, self.h1))
        self.assertEqual(self.client.get(reverse("profiles:horse_edit", args=[self.h1.pk])).status_code, 403)
        self.assertFalse(services.can_view_horse_documents(self.other, self.h1))
        self.assertContains(self.client.get(reverse("profiles:horse", args=[self.h1.pk])), "Отказаться от доступа")

    def test_decline_gives_nothing(self):
        self.grant()
        o = Offer.objects.get(kind=Offer.Kind.HORSE_ACCESS)
        self.client.force_login(self.other)
        self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "decline"})
        self.assertFalse(HorseAccess.objects.exists())

    def test_only_manager_can_grant_and_revoke(self):
        HorseAccess.objects.create(horse=self.h1, user=self.other, granted_by=self.coach)
        stranger = mkuser("+79990001777")
        for actor in (self.other, stranger):  # получатель доступа и посторонний не могут раздавать доступ
            self.client.force_login(actor)
            r = self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "grant"]), {"phone": "+79990001888"})
            self.assertEqual(r.status_code, 403)
            r = self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "revoke"]), {"access": HorseAccess.objects.get().pk})
            self.assertEqual(r.status_code, 403)
        self.assertFalse(Offer.objects.filter(kind=Offer.Kind.HORSE_ACCESS).exists())
        self.client.logout()
        r = self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "grant"]), {"phone": "+79990001888"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])
        self.client.force_login(self.coach)
        self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "revoke"]), {"access": HorseAccess.objects.get().pk})
        self.assertFalse(HorseAccess.objects.exists())

    def test_foreign_user_cannot_answer_invite(self):
        self.grant()
        o = Offer.objects.get(kind=Offer.Kind.HORSE_ACCESS)
        self.client.force_login(mkuser("+79990001778"))
        r = self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "accept"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(HorseAccess.objects.exists())

    def test_recipient_can_leave_and_stranger_cannot(self):
        HorseAccess.objects.create(horse=self.h1, user=self.other, granted_by=self.coach)
        self.client.force_login(mkuser("+79990001779"))
        self.assertEqual(self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "leave"])).status_code, 403)
        self.client.force_login(self.other)
        self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "leave"]))
        self.assertFalse(HorseAccess.objects.exists())

    def test_invite_after_transfer_is_void(self):
        self.grant("+79990001779")
        o = Offer.objects.get(kind=Offer.Kind.HORSE_ACCESS)
        self.h1.manager = self.other
        self.h1.save()
        u = mkuser("+79990001779")
        self.client.force_login(u)
        self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "accept"})
        self.assertFalse(HorseAccess.objects.exists())
        o.refresh_from_db()
        self.assertEqual(o.state, Offer.State.CANCELLED)

    def test_duplicate_and_self_invites_rejected(self):
        self.grant()
        self.grant()
        self.assertEqual(Offer.objects.filter(kind=Offer.Kind.HORSE_ACCESS).count(), 1)
        self.grant(self.coach.phone)
        self.assertEqual(Offer.objects.filter(kind=Offer.Kind.HORSE_ACCESS).count(), 1)

    def test_apply_page_lists_shared_horse(self):
        HorseAccess.objects.create(horse=self.h1, user=self.other, granted_by=self.coach)
        self.client.force_login(self.other)
        r = self.client.get(reverse("entries:apply", args=[self.t.pk]))
        self.assertIn(self.h1, r.context["horses"])
