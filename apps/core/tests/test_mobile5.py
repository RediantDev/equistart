"""Тест 5 (мобильная версия, 27.10.2026): поиск, восстановление пароля, роли, клубы, Положение."""
import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.clubs.models import Club, ClubNews, ClubPhoto
from apps.core import search
from apps.entries.tests.test_entries import TMP, Base, mkuser


def png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return SimpleUploadedFile("a.png", buf.getvalue(), content_type="image/png")


class SearchUnitTests(TestCase):
    def test_terms_ignore_org_words_quotes_and_yo(self):
        self.assertEqual(search.terms("КСК «Белая роща»"), ["белая", "роща"])
        self.assertEqual(search.terms("кск"), ["кск"])
        self.assertTrue(search.matches("КСК «Белая роща», д. Берёзки", "белая роща березки"))
        self.assertFalse(search.matches("КСК «Пегас»", "белая"))


@override_settings(MEDIA_ROOT=TMP)
class CalendarSearchTests(Base):
    def setUp(self):
        super().setUp()
        v = self.t.venue
        v.name, v.settlement = "КСК «Белая роща»", "д. Берёзки"
        v.save()

    def test_find_by_venue_without_kck_and_with_yo(self):
        url = reverse("core:calendar")
        for q in ("белая роща", "кск Белая Роща", "«Белая роща»", "березки", "Московская"):
            r = self.client.get(url, {"q": q})
            self.assertContains(r, self.t.pub_title, msg_prefix=q)
        self.assertNotContains(self.client.get(url, {"q": "пегас"}), self.t.pub_title)

    def test_only_active_disciplines_in_filter(self):
        r = self.client.get(reverse("core:calendar"))
        self.assertContains(r, "Выездка")
        self.assertNotContains(r, "Вольтижировка")

    def test_regulation_page_public_and_hidden_for_drafts(self):
        r = self.client.get(reverse("tournaments:public_regulation_page", args=[self.t.pk]))
        self.assertContains(r, "← К турниру")
        r = self.client.get(reverse("tournaments:public_regulation", args=[self.t.pk]))
        self.assertEqual(r.headers.get("X-Frame-Options"), "SAMEORIGIN")
        draft = self.make_tournament()
        draft.publication = "draft"
        draft.save(update_fields=["publication"])
        self.assertEqual(self.client.get(reverse("tournaments:public_regulation_page", args=[draft.pk])).status_code, 404)

    def test_clubs_search_shows_venue_when_no_club(self):
        r = self.client.get(reverse("clubs:list"), {"q": "белая роща"})
        self.assertContains(r, "Клуб ещё не завёл страницу")
        self.assertContains(r, "Белая роща")


@override_settings(MEDIA_ROOT=TMP)
class ClubUploadTests(Base):
    def setUp(self):
        super().setUp()
        self.cu = mkuser("+79990002001", is_club=True)
        self.club = Club.objects.create(manager=self.cu, name="КСК «Пегас»", region="mos", settlement="Москва", address="ул. 1", phone="+79990002001")
        self.client.force_login(self.cu)

    def test_several_photos_once(self):
        data = {"file": [png(), png(), png()], "once": "tok1"}
        self.client.post(reverse("clubs:photo_add"), data)
        self.assertEqual(ClubPhoto.objects.filter(club=self.club).count(), 3)
        self.client.post(reverse("clubs:photo_add"), {"file": [png()], "once": "tok1"})  # повторное нажатие
        self.assertEqual(ClubPhoto.objects.filter(club=self.club).count(), 3)

    def test_news_repeat_submit_no_duplicate(self):
        data = {"title": "Новость", "text": "Текст", "once": "n1"}
        self.client.post(reverse("clubs:news_new"), data)
        self.client.post(reverse("clubs:news_new"), data)
        self.assertEqual(ClubNews.objects.filter(club=self.club).count(), 1)

    def test_owner_sees_draft_link_others_404(self):
        r = self.client.get(reverse("clubs:mine"))
        self.assertContains(r, "Посмотреть страницу клуба")
        self.assertEqual(self.client.get(reverse("clubs:detail", args=[self.club.pk])).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("clubs:detail", args=[self.club.pk])).status_code, 404)

    def test_club_role_sees_add_button(self):
        Club.objects.all().delete()
        self.assertContains(self.client.get(reverse("clubs:list")), "+ Добавить клуб")


@override_settings(SMS_BACKEND="console")
class PasswordResetTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("+79990003001", "Old-pass-2026!", last_name="Тест", first_name="Юзер")

    def _request(self, phone):
        with mock.patch("apps.accounts.sms.send") as m:
            r = self.client.post(reverse("accounts:password_reset"), {"phone": phone})
        return r, (m.call_args[0][1].split("пароля ")[1][:6] if m.called else None)

    def test_flow(self):
        r, code = self._request("+7 999 000-30-01")
        self.assertRedirects(r, reverse("accounts:password_reset_confirm"), fetch_redirect_response=False)
        self.assertTrue(code)
        r = self.client.post(reverse("accounts:password_reset_confirm"), {"code": "000000" if code != "000000" else "111111", "password1": "New-pass-2026!", "password2": "New-pass-2026!"})
        self.assertContains(r, "Неверный код")
        r = self.client.post(reverse("accounts:password_reset_confirm"), {"code": code, "password1": "New-pass-2026!", "password2": "New-pass-2026!"})
        self.assertRedirects(r, reverse("accounts:cabinet"), fetch_redirect_response=False)
        self.u.refresh_from_db()
        self.assertTrue(self.u.check_password("New-pass-2026!"))

    def test_unknown_phone_same_answer_no_sms(self):
        r, code = self._request("+7 999 000-99-99")
        self.assertRedirects(r, reverse("accounts:password_reset_confirm"), fetch_redirect_response=False)
        self.assertIsNone(code)
        r = self.client.post(reverse("accounts:password_reset_confirm"), {"code": "123456", "password1": "New-pass-2026!", "password2": "New-pass-2026!"})
        self.assertContains(r, "Неверный код")

    def test_login_has_forgot_link_and_one_account_per_phone(self):
        self.assertContains(self.client.get(reverse("accounts:login")), reverse("accounts:password_reset"))
        r = self.client.post(reverse("accounts:register"), {"phone": "+79990003001"})
        self.assertContains(r, "уже зарегистрирован")


class MultiRoleTests(TestCase):
    def test_add_several_roles_at_once(self):
        u = User.objects.create_user("+79990004001", "Konkur-2026!", last_name="Тест", first_name="Юзер")
        self.client.force_login(u)
        self.client.post(reverse("accounts:add_role"), {"category": ["judge", "club"]})
        u.refresh_from_db()
        self.assertTrue(u.is_judge and u.is_club)


class ModeratorsPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("+79990005001", "Konkur-2026!", last_name="Иванов", first_name="Иван", is_moderator=True, is_superuser=True, is_staff=True)
        self.mod = User.objects.create_user("+79990005002", "Konkur-2026!", last_name="Мод", first_name="Мод", is_moderator=True)
        self.u = User.objects.create_user("+79990005003", "Konkur-2026!", last_name="Новый", first_name="Юзер")

    def test_only_owner(self):
        url = reverse("moderation:moderators")
        self.assertEqual(self.client.get(url).status_code, 302)  # гость → вход
        self.client.force_login(self.mod)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"action": "add", "phone": self.u.phone}).status_code, 403)

    def test_add_and_remove_several(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("moderation:moderators"), {"action": "add", "phone": "+7 999 000-50-03"})
        self.u.refresh_from_db()
        self.assertTrue(self.u.is_moderator)
        self.client.post(reverse("moderation:moderators"), {"action": "remove", "user": self.mod.pk})
        self.client.post(reverse("moderation:moderators"), {"action": "remove", "user": self.owner.pk})
        self.mod.refresh_from_db(); self.owner.refresh_from_db()
        self.assertFalse(self.mod.is_moderator)
        self.assertTrue(self.owner.is_moderator)


@override_settings(MEDIA_ROOT=TMP)
class HorseTests(Base):
    def test_search_detects_name_or_number(self):
        from apps.profiles import services as ps

        self.h1.fksr_number = "123456"
        self.h1.save()
        self.assertIn(self.h1, list(ps.search_horses("123456", "auto")))
        self.assertIn(self.h1, list(ps.search_horses("бар", "auto")))
        self.assertIn(self.h2, list(ps.search_horses(self.h2.code.lower(), "auto")))

    def test_doc_upload_jpeg_and_clear_error(self):
        self.client.force_login(self.coach)
        url = reverse("profiles:horse_action", args=[self.h1.pk, "upload"])
        r = self.client.post(url, {"kind": "passport", "file": SimpleUploadedFile("a.jpg", b"\xff\xd8\xff" + b"0" * 50)}, follow=True)
        self.assertEqual(self.h1.documents.count(), 1, r.content.decode()[:0])
        r = self.client.post(url, {"kind": "passport"}, follow=True)
        self.assertContains(r, "Файл: выберите файл документа")

    def test_my_horses_has_search_field(self):
        self.client.force_login(self.coach)
        r = self.client.get(reverse("profiles:horses"))
        self.assertContains(r, 'name="q"')
        self.assertNotContains(r, ">Найти лошадь</a>")


@override_settings(MEDIA_ROOT=TMP)
class StableContactTests(Base):
    def test_one_responsible_set_by_organizer(self):
        from apps.tournaments.models import TeamMember

        s1 = mkuser("+79990006001", last_name="Первая", first_name="Конюшня")
        s2 = mkuser("+79990006002", last_name="Вторая", first_name="Конюшня")
        m1 = TeamMember.objects.create(tournament=self.t, phone=s1.phone, user=s1, role="stable", state="accepted", invited_by=self.org)
        m2 = TeamMember.objects.create(tournament=self.t, phone=s2.phone, user=s2, role="stable", state="accepted", invited_by=self.org)
        from apps.stables.views import _contacts

        self.assertEqual([c["phone"] for c in _contacts(self.t)], [m1.phone])  # по умолчанию — первый
        url = reverse("tournaments:team_stable_contact", args=[self.t.pk, m2.pk])
        self.assertEqual(self.client.post(url).status_code, 302)  # гость → вход
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code, 403)  # чужой
        self.client.force_login(self.org)
        self.client.post(url)
        self.assertEqual([c["phone"] for c in _contacts(self.t)], [m2.phone])
        self.client.post(reverse("tournaments:team_stable_contact", args=[self.t.pk, m1.pk]))
        self.assertEqual(TeamMember.objects.filter(tournament=self.t, stable_contact=True).count(), 1)


@override_settings(MEDIA_ROOT=TMP)
class MyCompetitionsTests(Base):
    def test_one_page_for_coach_and_linked_athlete(self):
        from apps.entries import services as es

        self.a1.linked_user = self.kid
        self.a1.save()
        app = self.submit(self.coach, [es.Line(self.a1, self.h1, self.c_kids), es.Line(self.a2, self.h1, self.c_open)])
        url = reverse("entries:competitions")
        self.assertEqual(self.client.get(url).status_code, 302)  # гость → вход
        self.client.force_login(self.kid)
        r = self.client.get(url)
        self.assertContains(r, "Смирнова")
        self.assertContains(r, "заявил тренер")
        self.assertNotContains(r, "Орлов")  # чужие позиции заявки не видны
        self.assertNotContains(r, f"Заявка №{app.pk}")
        self.client.force_login(self.coach)
        r = self.client.get(url)
        self.assertContains(r, "Орлов")
        self.assertContains(r, f"Заявка №{app.pk}")
        self.assertContains(self.client.get(url + "?tab=apps"), f"№{app.pk}")
        self.assertContains(self.client.get(url + "?tab=past"), "Прошедших стартов пока нет")
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(url), self.t.pub_title)



@override_settings(MEDIA_ROOT=TMP)
class ClubRedesignTests(Base):
    def setUp(self):
        super().setUp()
        self.cu = mkuser("+79990007001", is_club=True)
        self.client.force_login(self.cu)

    def test_wizard_then_dashboard_tabs_and_cover(self):
        r = self.client.get(reverse("clubs:mine"))
        self.assertContains(r, "1. Название и адрес")  # нет клуба — мастер
        data = {"name": "КСК «Вега»", "region": "mos", "settlement": "Москва", "address": "ул. 1", "phone": "+79990007001"}
        self.client.post(reverse("clubs:mine"), data)
        club = Club.objects.get(manager=self.cu)
        r = self.client.get(reverse("clubs:mine"))
        self.assertContains(r, "Страница заполнена")  # кабинет
        self.assertContains(self.client.get(reverse("clubs:mine") + "?edit=1"), "2. Контакты")
        self.client.post(reverse("clubs:photo_add"), {"file": [png(), png()], "once": "c1"})
        p1, p2 = list(ClubPhoto.objects.filter(club=club))
        self.client.post(reverse("clubs:photo_cover", args=[p2.pk]))
        self.assertEqual(list(ClubPhoto.objects.filter(club=club).values_list("pk", flat=True)), [p2.pk, p1.pk])
        for tab in ("tournaments", "news", "photos", "about"):
            self.assertEqual(self.client.get(reverse("clubs:detail", args=[club.pk]) + "?tab=" + tab).status_code, 200)
        self.assertContains(self.client.get(reverse("clubs:detail", args=[club.pk]) + "?tab=about"), "yandex.ru/maps")
        # чужой и гость не меняют обложку
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("clubs:photo_cover", args=[p1.pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("clubs:photo_cover", args=[p1.pk])).status_code, 302)
