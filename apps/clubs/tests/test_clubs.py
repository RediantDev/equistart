import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.clubs import services
from apps.clubs.models import Club, ClubNews
from apps.entries.tests.test_entries import TMP, Base, mkuser
from apps.tournaments.models import Tournament

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


@override_settings(MEDIA_ROOT=TMP)
class ClubBase(Base):
    def setUp(self):
        super().setUp()
        self.cu = mkuser("+79990002001", is_club=True, last_name="Петров", first_name="Олег")
        self.club = Club.objects.create(manager=self.cu, name="КСК «Пегас»", region="mos", settlement="Москва", address="ул. Конная, 1", phone="+79990002001")

    def submit_confirm(self, club=None):
        club = club or self.club
        services.submit(self.cu, club.pk)
        return services.confirm(self.mod, club.pk)


class ClubFlowTests(ClubBase):
    def test_hidden_until_confirmed(self):
        url = reverse("clubs:detail", args=[self.club.pk])
        self.assertEqual(self.client.get(url).status_code, 404)  # гость
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)  # чужой
        self.client.force_login(self.cu)
        self.assertEqual(self.client.get(url).status_code, 200)  # свой
        self.assertNotContains(self.client.get(reverse("clubs:list")), "Пегас")

    def test_submit_requires_contacts(self):
        self.club.phone = ""
        self.club.save()
        with self.assertRaises(ValueError):
            services.submit(self.cu, self.club.pk)

    def test_confirm_creates_venue_and_public(self):
        c = self.submit_confirm()
        self.assertTrue(c.venue.confirmed)
        self.assertEqual(c.venue.name, "КСК «Пегас»")
        r = self.client.get(reverse("clubs:list") + "?q=пегас")
        self.assertContains(r, "Пегас")
        self.assertNotContains(self.client.get(reverse("clubs:list") + "?region=spb"), "Пегас")

    def test_return_needs_comment(self):
        services.submit(self.cu, self.club.pk)
        with self.assertRaises(ValueError):
            services.return_for_fixes(self.mod, self.club.pk, " ")
        services.return_for_fixes(self.mod, self.club.pk, "Укажите номер дома")
        self.client.force_login(self.cu)
        self.assertContains(self.client.get(reverse("clubs:mine")), "Укажите номер дома")

    def test_rename_resubmits_public_keeps_old(self):
        self.submit_confirm()
        self.client.force_login(self.cu)
        data = {"name": "КСК «Пегас-Новый»", "region": "mos", "settlement": "Москва", "address": "ул. Конная, 1", "phone": "+79990002001",
                "description": "", "email": "", "site": "", "socials": ""}
        self.client.post(reverse("clubs:mine"), data)
        self.club.refresh_from_db()
        self.assertEqual(self.club.status, "review")
        self.client.logout()
        r = self.client.get(reverse("clubs:detail", args=[self.club.pk]))
        self.assertContains(r, "КСК «Пегас»")
        self.assertNotContains(r, "Пегас-Новый")
        services.confirm(self.mod, self.club.pk)
        self.assertContains(self.client.get(reverse("clubs:detail", args=[self.club.pk])), "Пегас-Новый")

    def test_description_edit_does_not_resubmit(self):
        self.submit_confirm()
        self.club.refresh_from_db()
        self.club.description = "Манеж 20×60"
        services.save(self.cu, self.club, ["description"])
        self.assertEqual(self.club.status, "confirmed")

    def test_similar(self):
        other = Club.objects.create(manager=mkuser("+79990002002", is_club=True), name="Пегас", region="mos", settlement="москва", address="x")
        self.assertIn(other, services.similar(self.club))

    def test_mine_requires_club_role(self):
        self.client.force_login(self.coach)
        self.assertRedirects(self.client.get(reverse("clubs:mine")), reverse("accounts:add_role"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("moderation:clubs")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("clubs:mine")).status_code, 302)

    def test_moderation_screens(self):
        services.submit(self.cu, self.club.pk)
        self.client.force_login(self.mod)
        self.assertContains(self.client.get(reverse("moderation:clubs")), "Пегас")
        self.client.post(reverse("moderation:club", args=[self.club.pk]), {"decision": "confirm"})
        self.club.refresh_from_db()
        self.assertTrue(self.club.is_public)


class ClubContentTests(ClubBase):
    def test_photo_signature_and_foreign_delete(self):
        self.client.force_login(self.cu)
        self.client.post(reverse("clubs:photo_add"), {"file": SimpleUploadedFile("x.jpg", b"GIF89a....", "image/jpeg")})
        self.assertEqual(self.club.photos.count(), 0)
        self.client.post(reverse("clubs:photo_add"), {"file": SimpleUploadedFile("x.png", PNG, "image/png")})
        p = self.club.photos.get()
        # пока клуб не подтверждён, файл гостю не отдаётся
        self.client.logout()
        self.assertEqual(self.client.get(reverse("clubs:file", args=["photo", p.pk])).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("clubs:photo_delete", args=[p.pk])).status_code, 403)
        self.submit_confirm()
        self.client.logout()
        self.assertEqual(self.client.get(reverse("clubs:file", args=["photo", p.pk])).status_code, 200)

    def test_news_with_tournament_and_hide(self):
        self.submit_confirm()
        self.club.refresh_from_db()
        self.t.venue = self.club.venue
        self.t.save()
        Tournament.objects.filter(pk=self.t.pk).update(publication="published")
        self.client.force_login(self.cu)
        r = self.client.post(reverse("clubs:news_new"), {"title": "Кубок клуба", "text": "Ждём всех", "tournament": self.t.pk,
                                                          "photos": SimpleUploadedFile("n.png", PNG, "image/png")})
        self.assertEqual(r.status_code, 302)
        n = ClubNews.objects.get()
        self.assertEqual(n.tournament_id, self.t.pk)
        self.assertEqual(n.photos.count(), 1)
        # чужой не может править новость
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("clubs:news_edit", args=[n.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("clubs:news_delete", args=[n.pk])).status_code, 403)
        self.client.logout()
        self.assertContains(self.client.get(reverse("clubs:detail", args=[self.club.pk])), "Кубок клуба")
        with self.assertRaises(ValueError):
            services.hide_news(self.mod, n.pk, "")
        services.hide_news(self.mod, n.pk, "Реклама")
        self.assertNotContains(self.client.get(reverse("clubs:detail", args=[self.club.pk])), "Кубок клуба")
        self.assertEqual(self.client.get(reverse("clubs:file", args=["news", n.photos.get().pk])).status_code, 404)

    def test_club_venue_selectable_and_calendar_filter(self):
        from apps.tournaments.forms import BasicsForm
        self.submit_confirm()
        self.club.refresh_from_db()
        self.assertIn(self.club.venue, BasicsForm(user=self.org).fields["venue"].queryset)
        snap_title = self.t.pub_title or self.t.title
        self.t.venue = self.club.venue
        self.t.save()
        Tournament.objects.filter(pk=self.t.pk).update(publication="published", pub_title=snap_title, pub_starts_on=self.start, pub_ends_on=self.start)
        r = self.client.get(reverse("core:calendar") + f"?club={self.club.pk}")
        self.assertContains(r, snap_title)
        self.assertContains(r, "КСК «Пегас»")
        self.assertContains(self.client.get(reverse("clubs:detail", args=[self.club.pk])), snap_title)


class ClubTeamTests(ClubBase):
    def setUp(self):
        super().setUp()
        self.helper = mkuser("+79990002003", last_name="Иванова", first_name="Ольга")

    def invite_accept(self):
        m = services.invite_manager(self.cu, self.club.pk, "8 999 000-20-03")
        self.client.force_login(self.helper)
        self.assertContains(self.client.get(reverse("tournaments:invitations")), self.club.name)
        self.client.post(reverse("clubs:invite_respond", args=[m.pk]), {"decision": "accept"})
        m.refresh_from_db()
        return m

    def test_invite_accept_manage(self):
        m = self.invite_accept()
        self.assertEqual(m.state, "accepted")
        self.helper.refresh_from_db()
        self.assertTrue(self.helper.is_club)
        self.assertEqual(self.client.get(reverse("clubs:mine")).status_code, 200)
        r = self.client.post(reverse("clubs:photo_add"), {"file": SimpleUploadedFile("x.png", PNG, "image/png")})
        self.assertEqual(self.club.photos.count(), 1)
        # управляющий не приглашает других
        self.client.post(reverse("clubs:team"), {"action": "invite", "phone": "+79990002009"})
        self.assertEqual(self.club.members.count(), 1)

    def test_foreign_cannot_accept(self):
        m = services.invite_manager(self.cu, self.club.pk, "+79990002003")
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("clubs:invite_respond", args=[m.pk]), {"decision": "accept"}).status_code, 403)

    def test_duplicate_invite_and_own_phone(self):
        services.invite_manager(self.cu, self.club.pk, "+79990002003")
        with self.assertRaises(ValueError):
            services.invite_manager(self.cu, self.club.pk, "+79990002003")
        with self.assertRaises(ValueError):
            services.invite_manager(self.cu, self.club.pk, self.cu.phone)

    def test_transfer_and_leave(self):
        m = self.invite_accept()
        self.client.force_login(self.cu)
        r = self.client.post(reverse("clubs:team"), {"action": "transfer", "member": m.pk})
        self.assertContains(r, "Передать клуб?")
        self.client.post(reverse("clubs:team"), {"action": "transfer", "member": m.pk, "confirm": "1"})
        self.club.refresh_from_db()
        self.assertEqual(self.club.manager, self.helper)
        self.assertTrue(services.can_manage(self.cu, self.club))
        self.assertFalse(services.is_owner(self.cu, self.club))
        # бывший владелец уходит сам
        mm = self.club.members.get(user=self.cu)
        self.client.post(reverse("clubs:team"), {"action": "remove", "member": mm.pk})
        self.assertFalse(services.can_manage(self.cu, self.club))

    def test_one_club_per_account(self):
        other_club = Club.objects.create(manager=self.helper, name="КСК «Другой»", region="mos", settlement="Москва", address="x", phone="1")
        self.helper.is_club = True
        self.helper.save()
        m = services.invite_manager(self.cu, self.club.pk, "+79990002003")
        with self.assertRaises(ValueError):
            services.respond(self.helper, m.pk, True)
        self.assertTrue(other_club.pk)

    def test_team_page_requires_club(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("clubs:team")).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("clubs:team")).status_code, 302)


class ClubMergeTests(ClubBase):
    def test_merge(self):
        self.submit_confirm()
        u2 = mkuser("+79990002004", is_club=True)
        dup = Club.objects.create(manager=u2, name="Пегас", region="mos", settlement="Москва", address="Конная 1", phone="2")
        services.submit(u2, dup.pk)
        services.confirm(self.mod, dup.pk)
        dup.refresh_from_db()
        self.club.refresh_from_db()
        self.t.venue = dup.venue
        self.t.save()
        ClubNews.objects.create(club=dup, title="Старт", text="x")
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("moderation:club", args=[dup.pk]), {"decision": "merge", "main": self.club.pk}).status_code, 403)
        self.client.force_login(self.mod)
        self.client.post(reverse("moderation:club", args=[dup.pk]), {"decision": "merge", "main": self.club.pk})
        dup.refresh_from_db()
        self.t.refresh_from_db()
        self.assertEqual(dup.merged_into, self.club)
        self.assertEqual(self.t.venue_id, self.club.venue_id)
        self.assertFalse(dup.venue.confirmed)
        self.assertEqual(self.club.news.count(), 1)
        self.assertRedirects(self.client.get(reverse("clubs:detail", args=[dup.pk])), reverse("clubs:detail", args=[self.club.pk]), status_code=301)
        self.assertNotContains(self.client.get(reverse("clubs:list")), "Конная 1")
        self.assertFalse(services.can_manage(u2, dup))
        with self.assertRaises(ValueError):
            services.merge(self.mod, self.club.pk, dup.pk)
