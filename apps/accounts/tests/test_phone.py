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
        for raw in ["", "12345", "+7 495 123-45-67", "8999123456", "999-abc-12", "7+9991234567", "+7999123456789", "+375 17 123-45-67",
                    "+375 29 123-45-6", "+0 123 456 789", "+1 23", "+998 90 123 45", "+1234567890123456", "123456789012"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    normalize_phone(raw)

    def test_other_countries(self):
        # решение 25.10.2026: номера любых стран в международном формате
        cases = {
            "+998 90 123-45-67": "+998901234567",
            "998901234567": "+998901234567",
            "+996 555 123 456": "+996555123456",
            "+380 50 123 45 67": "+380501234567",
            "+1 202 555 0100": "+12025550100",
            "+49 151 23456789": "+4915123456789",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), expected)

    def test_format(self):
        self.assertEqual(format_phone("+79991234567"), "+7 999 123-45-67")
        self.assertEqual(format_phone("+375291234567"), "+375 29 123-45-67")
        self.assertEqual(format_phone("+998901234567"), "+998 90 123-45-67")
        self.assertEqual(format_phone("+996555123456"), "+996 555 123-456")
        self.assertEqual(format_phone("+12025550100"), "+12025550100")
