from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User

PASSWORD = "Konkur-2026!"


def make_user(phone="+79990000001", **extra):
    defaults = {"last_name": "Иванова", "first_name": "Анна", "is_athlete": True}
    defaults.update(extra)
    return User.objects.create_user(phone, PASSWORD, **defaults)


class RegistrationTests(TestCase):
    def setUp(self):
        cache.clear()

    def post(self, **overrides):
        data = {
            "phone": "8 (999) 111-22-33",
            "password1": PASSWORD,
            "password2": PASSWORD,
            "last_name": "Петров",
            "first_name": "Пётр",
            "middle_name": "",
            "email": "",
            "categories": ["coach", "organizer"],
            "pd_consent": "on",
        }
        data.update(overrides)
        return self.client.post(reverse("accounts:register"), data)

    def test_register_creates_user_and_logs_in(self):
        resp = self.post()
        self.assertRedirects(resp, reverse("accounts:cabinet"))
        user = User.objects.get()
        self.assertEqual(user.phone, "+79991112233")
        self.assertTrue(user.is_coach and user.is_organizer)
        self.assertFalse(user.is_athlete)
        self.assertFalse(user.is_moderator)
        self.assertIsNotNone(user.pd_consent_at)
        self.assertFalse(user.phone_verified)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_duplicate_phone_in_other_format_rejected(self):
        make_user(phone="+79991112233")
        resp = self.post(phone="+7 999 111 22 33")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "уже зарегистрирован")
        self.assertEqual(User.objects.count(), 1)

    def test_consent_required(self):
        data = self.post(pd_consent="")
        self.assertEqual(User.objects.count(), 0)
        self.assertContains(data, "Без согласия")

    def test_category_required_and_moderator_not_selectable(self):
        self.post(categories=[])
        self.assertEqual(User.objects.count(), 0)
        resp = self.post(categories=["moderator"])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_password_rules(self):
        self.post(password1="12345678", password2="12345678")
        self.post(password2="other-password-1")
        self.assertEqual(User.objects.count(), 0)

    def test_db_constraint_on_unnormalized_phone(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(phone="89991234567", last_name="X", first_name="Y")])


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()

    def login(self, phone="+79990000001", password=PASSWORD, **extra):
        return self.client.post(reverse("accounts:login"), {"phone": phone, "password": password}, **extra)

    def test_login_any_phone_format(self):
        resp = self.login(phone="8 999 000 00 01")
        self.assertRedirects(resp, reverse("accounts:cabinet"))

    def test_wrong_password(self):
        resp = self.login(password="wrong-password")
        self.assertContains(resp, "Неверный телефон или пароль")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(LOGIN_MAX_FAILURES_PER_PHONE=3)
    def test_lockout_after_failures_even_with_right_password(self):
        for _ in range(3):
            self.login(password="wrong-password")
        resp = self.login()
        self.assertContains(resp, "Слишком много неудачных попыток")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(LOGIN_MAX_FAILURES_PER_IP=4, LOGIN_MAX_FAILURES_PER_PHONE=100)
    def test_lockout_per_ip_across_phones(self):
        for i in range(4):
            self.login(phone=f"+7999000010{i}", password="x")
        resp = self.login()
        self.assertContains(resp, "Слишком много неудачных попыток")

    def test_inactive_user_message_only_with_right_password(self):
        self.user.is_active = False
        self.user.save()
        self.assertContains(self.login(password="wrong-password"), "Неверный телефон или пароль")
        self.assertContains(self.login(), "Аккаунт заблокирован")

    def test_open_redirect_blocked(self):
        resp = self.client.post(
            reverse("accounts:login") + "?next=https://evil.example/",
            {"phone": "+79990000001", "password": PASSWORD, "next": "https://evil.example/"},
        )
        self.assertRedirects(resp, reverse("accounts:cabinet"))

    def test_next_respected(self):
        resp = self.client.post(reverse("accounts:login"), {"phone": "+79990000001", "password": PASSWORD, "next": "/cabinet/add-role/"})
        self.assertRedirects(resp, "/cabinet/add-role/")

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


class GuestTests(TestCase):
    def test_guest_sees_calendar_without_management(self):
        resp = self.client.get(reverse("core:calendar"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Войти")
        self.assertNotContains(resp, "Выйти")
        self.assertNotContains(resp, "Кабинет")

    def test_cabinet_requires_login(self):
        resp = self.client.get(reverse("accounts:cabinet"))
        self.assertRedirects(resp, reverse("accounts:login") + "?next=/cabinet/")

    def test_admin_closed_for_regular_users(self):
        user = make_user()
        self.client.force_login(user)
        resp = self.client.get("/manage/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/manage/login/", resp["Location"])


class CabinetTests(TestCase):
    def setUp(self):
        self.user = make_user(is_athlete=True, is_coach=True)
        self.client.force_login(self.user)

    def test_mode_switch_persists(self):
        self.client.post(reverse("accounts:switch_mode"), {"mode": "coach"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.current_mode, "coach")
        resp = self.client.get(reverse("accounts:cabinet"))
        self.assertContains(resp, "Кого вы ведёте")

    def test_cannot_switch_to_foreign_mode(self):
        for mode in ["organizer", "moderator", "admin"]:
            with self.subTest(mode=mode):
                resp = self.client.post(reverse("accounts:switch_mode"), {"mode": mode})
                self.assertEqual(resp.status_code, 400)

    def test_add_role_keeps_profile(self):
        resp = self.client.post(reverse("accounts:add_role"), {"category": "organizer"})
        self.assertRedirects(resp, reverse("accounts:cabinet"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_organizer and self.user.is_athlete and self.user.is_coach)
        self.assertEqual(self.user.current_mode, "organizer")
        self.assertEqual(User.objects.count(), 1)

    def test_cannot_add_moderator_or_existing_role(self):
        for cat in ["moderator", "athlete"]:
            with self.subTest(cat=cat):
                resp = self.client.post(reverse("accounts:add_role"), {"category": cat})
                self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_moderator)

    def test_moderator_mode_only_when_assigned(self):
        self.assertNotIn("moderator", [m for m, _ in self.user.available_modes()])
        self.user.is_moderator = True
        self.user.save()
        self.assertIn("moderator", [m for m, _ in self.user.available_modes()])
