from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts import verification
from apps.accounts.models import PhoneCode, User


@override_settings(PHONE_VERIFICATION_REQUIRED=True, SMS_BACKEND="console")
class PhoneVerificationTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("+79990002001", "Konkur-2026!", last_name="Тест", first_name="Юзер")
        self.client.force_login(self.u)

    def _send(self):
        with mock.patch("apps.accounts.sms.send") as m:
            verification.send_code(self.u)
            return m.call_args[0][1].split("код ")[1][:6]

    def test_redirects_until_verified(self):
        r = self.client.get(reverse("accounts:cabinet"))
        self.assertRedirects(r, reverse("accounts:verify_phone") + "?next=/cabinet/", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:privacy")).status_code, 200)

    def test_code_flow(self):
        code = self._send()
        self.assertNotEqual(PhoneCode.objects.get().code_hash, code)  # хранится только хеш
        r = self.client.post(reverse("accounts:verify_phone"), {"code": "000000" if code != "000000" else "111111"})
        self.assertContains(r, "Неверный код")
        r = self.client.post(reverse("accounts:verify_phone") + "?next=/cabinet/", {"code": code})
        self.assertRedirects(r, "/cabinet/", fetch_redirect_response=False)
        self.u.refresh_from_db()
        self.assertTrue(self.u.phone_verified)
        self.assertEqual(self.client.get(reverse("accounts:cabinet")).status_code, 200)

    def test_resend_limit_and_attempts(self):
        code = self._send()
        with self.assertRaises(ValueError):
            self._send()
        for _ in range(verification.MAX_ATTEMPTS):
            with self.assertRaises(ValueError):
                verification.check_code(self.u, "999999" if code != "999999" else "888888")
        with self.assertRaises(ValueError):
            verification.check_code(self.u, code)  # после лимита попыток верный код не принимается

    def test_open_redirect_blocked(self):
        code = self._send()
        r = self.client.post(reverse("accounts:verify_phone") + "?next=//evil.example", {"code": code})
        self.assertRedirects(r, reverse("accounts:cabinet"), fetch_redirect_response=False)


class PasswordAndCommandsTests(TestCase):
    def test_change_password_and_commands(self):
        from io import StringIO

        from django.core.management import call_command

        u = User.objects.create_user("+79990002002", "Konkur-2026!", last_name="Тест", first_name="Юзер")
        self.assertEqual(self.client.get(reverse("accounts:password")).status_code, 302)
        self.client.force_login(u)
        r = self.client.post(reverse("accounts:password"), {"old_password": "Konkur-2026!", "new_password1": "Novyi-Parol-77", "new_password2": "Novyi-Parol-77"})
        self.assertRedirects(r, reverse("accounts:profile"))
        u.refresh_from_db()
        self.assertTrue(u.check_password("Novyi-Parol-77"))
        out = StringIO()
        call_command("set_password", "8 999 000 20 02", stdout=out)
        tmp = out.getvalue().split(": ")[1].split()[0]
        u.refresh_from_db()
        self.assertTrue(u.check_password(tmp))
        call_command("set_moderator", "+79990002002", stdout=StringIO())
        u.refresh_from_db()
        self.assertTrue(u.is_moderator)
