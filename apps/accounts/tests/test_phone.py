from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.accounts.phone import format_phone, normalize_phone


class NormalizePhoneTests(SimpleTestCase):
    def test_accepts_common_formats(self):
        for raw in ["+7 999 123-45-67", "8 (999) 123-45-67", "79991234567", "9991234567", " +7(999)1234567 "]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), "+79991234567")

    def test_rejects_invalid(self):
        for raw in ["", "12345", "+1 202 555 0100", "+7 495 123-45-67", "8999123456", "999-abc-12", "7+9991234567", "+7999123456789"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    normalize_phone(raw)

    def test_format(self):
        self.assertEqual(format_phone("+79991234567"), "+7 999 123-45-67")
