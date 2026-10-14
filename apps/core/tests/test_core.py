from django.test import TestCase


class HealthTests(TestCase):
    def test_live_and_ready(self):
        self.assertEqual(self.client.get("/health/live").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/health/ready").json(), {"status": "ok"})


class PagesTests(TestCase):
    def test_privacy_page(self):
        resp = self.client.get("/privacy/")
        self.assertContains(resp, "Политика обработки персональных данных")

    def test_security_headers(self):
        resp = self.client.get("/")
        self.assertEqual(resp["X-Frame-Options"], "DENY")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
