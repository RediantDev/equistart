from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.accounts.phone import format_phone, normalize_phone


class NormalizePhoneTests(SimpleTestCase):
    def test_accepts_common_formats(self):
        for raw in ["+7 999 123-45-67", "8 (999) 123-45-67", "79991234567", "9991234567", " +7(999)1234567 "]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), "+79991234567")

    def test_kazakhstan_and_belarus(self):
        cases = {
            "+7 701 123-45-67": "+77011234567",
            "8 (701) 123-45-67": "+77011234567",
            "+375 29 123-45-67": "+375291234567",
            "375291234567": "+375291234567",
            "80 29 123-45-67": "+375291234567",
            "+375 (44) 1234567": "+375441234567",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), expected)

    def test_rejects_invalid(self):
        for raw in ["", "12345", "+1 202 555 0100", "+7 495 123-45-67", "8999123456", "999-abc-12", "7+9991234567", "+7999123456789", "+375 17 123-45-67", "+375 29 123-45-6", "+380 50 123 45 67"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    normalize_phone(raw)

    def test_format(self):
        self.assertEqual(format_phone("+79991234567"), "+7 999 123-45-67")
        self.assertEqual(format_phone("+375291234567"), "+375 29 123-45-67")
