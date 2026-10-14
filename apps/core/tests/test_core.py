from django.test import TestCase


class HealthTests(TestCase):
    def test_live_and_ready(self):
        self.assertEqual(self.client.get("/health/live").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/health/ready").json(), {"status": "ok"})


class PagesTests(TestCase):
    def test_legal_pages(self):
        self.assertContains(self.client.get("/privacy/"), "Политика обработки персональных данных")
        self.assertContains(self.client.get("/consent/"), "Согласие на обработку персональных данных")
        resp = self.client.get("/publication-consent/")
        self.assertContains(resp, "разрешённых для распространения")
        self.assertContains(resp, "Не публикуются")

    def test_security_headers(self):
        resp = self.client.get("/")
        self.assertEqual(resp["X-Frame-Options"], "DENY")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
