"""Тест 5.1 (мобильная версия, 28.10.2026): регистрация, результаты по дням, фото, нижнее меню."""
import datetime
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.entries.tests.test_entries import TMP, Base, mkuser


class RegistrationDuplicatePhoneTests(TestCase):
    def setUp(self):
        User.objects.create_user("+79990008001", "Konkur-2026!", last_name="Тест", first_name="Юзер")

    def test_phone_check(self):
        url = reverse("accounts:phone_check")
        self.assertEqual(self.client.get(url, {"phone": "+7 999 000-80-01"}).json()["exists"], True)
        self.assertEqual(self.client.get(url, {"phone": "+7 999 000-80-02"}).json()["exists"], False)
        self.assertEqual(self.client.get(url, {"phone": "abc"}).json()["valid"], False)

    def test_duplicate_shows_login_popup_without_password_errors(self):
        r = self.client.post(reverse("accounts:register"), {
            "last_name": "Тест", "first_name": "Новый", "categories": ["athlete"], "phone": "+79990008001",
            "password1": "Konkur-2026!", "password2": "Другой-2026!",
        })
        self.assertContains(r, "Этот номер уже зарегистрирован. Войти в аккаунт?")
        self.assertNotContains(r, "Пароли не совпадают")


@override_settings(MEDIA_ROOT=TMP)
class PublicPageTests(Base):
    def test_results_open_all_days_by_default(self):
        from apps.tournaments.models import ProgramItem

        ProgramItem.objects.create(tournament=self.t, day=self.start + datetime.timedelta(days=1), discipline="jumping", name="Маршрут №2",
                                   program_type="manual", result_fields=["penalties"], heights=[100])
        self.t.pub_ends_on = self.start + datetime.timedelta(days=1)
        self.t.save(update_fields=["pub_ends_on"])
        r = self.client.get(reverse("tournaments:public", args=[self.t.pk]) + "?tab=results")
        self.assertEqual(r.context["day"], "all")

    def test_bottom_menu_for_guest_and_user(self):
        r = self.client.get(reverse("core:calendar"))
        self.assertContains(r, 'class="bnav"')
        self.assertContains(r, "<span>Войти</span>")
        self.client.force_login(self.coach)
        r = self.client.get(reverse("core:calendar"))
        self.assertContains(r, "<span>Кабинет</span>")
        self.assertContains(r, reverse("entries:competitions"))

    def test_moderator_sees_pult_in_cabinet(self):
        self.client.force_login(self.mod)
        self.assertContains(self.client.get(reverse("accounts:cabinet")), "Пульт модератора")


@override_settings(MEDIA_ROOT=TMP)
class ImageOptimizeTests(TestCase):
    def test_big_photo_is_resized_and_thumbnail_served(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("нет Pillow")
        from apps.clubs.models import Club, ClubPhoto
        from apps.clubs import services

        buf = io.BytesIO()
        Image.new("RGB", (3000, 2000), (120, 160, 200)).save(buf, "JPEG", quality=95)
        cu = mkuser("+79990008101", is_club=True)
        mod = mkuser("+79990008102", is_moderator=True)
        club = Club.objects.create(manager=cu, name="КСК «Тест»", region="mos", settlement="Москва", address="ул. 1", phone="+79990008101")
        self.client.force_login(cu)
        self.client.post(reverse("clubs:photo_add"), {"file": [SimpleUploadedFile("big.jpg", buf.getvalue(), "image/jpeg")], "once": "x1"})
        p = ClubPhoto.objects.get(club=club)
        with Image.open(p.file.path) as im:
            self.assertLessEqual(max(im.size), 1600)
        services.submit(cu, club.pk)
        services.confirm(mod, club.pk)
        r = self.client.get(reverse("clubs:file", args=["photo", p.pk]) + "?w=320")
        self.assertEqual(r.status_code, 200)
        self.assertIn("max-age=2592000", r["Cache-Control"])
        data = b"".join(r.streaming_content)
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.width, 320)
