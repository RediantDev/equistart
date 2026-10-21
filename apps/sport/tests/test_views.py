"""Конструктор езды: права и разбор листа."""

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.sport.models import DressageTest


def user(phone, **kw):
    return User.objects.create_user(phone, "Konkur-2026!", first_name="Т", last_name="Т", **kw)


MOVES = "1 | A X | Въезд рабочим галопом, остановка | 1\n2 | C | Поворот направо | 1\n3 | | Шаг | 2"


class DressageTestViews(TestCase):
    def setUp(self):
        self.org = user("+79990000201", is_organizer=True)
        self.other = user("+79990000202", is_organizer=True)
        self.athlete = user("+79990000203")

    def data(self, **kw):
        d = {"name": "Клубная езда №1", "kind": "standard", "arena": "20x40", "penalty_scheme": "fei_junior", "tie_rule": "share",
             "step": "0.5", "technique_movements": MOVES, "technique_collective": "Гармония | 2"}
        d.update(kw)
        return d

    def test_create_parse_and_privacy(self):
        self.client.force_login(self.org)
        resp = self.client.post(reverse("sport:test_new"), self.data())
        t = DressageTest.objects.get(name="Клубная езда №1")
        self.assertRedirects(resp, reverse("sport:test_detail", args=[t.pk]))
        self.assertEqual((t.owner, t.is_official), (self.org, False))
        part = t.parts[0]
        self.assertEqual([m["letters"] for m in part["movements"]], ["A X", "C", ""])
        self.assertEqual(part["movements"][2]["coef"], 2)
        self.assertEqual(str(t.max_points["technique"]), "60")  # (1 + 1 + 2 + 2) × 10
        # чужой организатор не видит и не правит
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("sport:test_detail", args=[t.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("sport:test_edit", args=[t.pk])).status_code, 403)
        # официальную езду правит только модератор
        official = DressageTest.objects.filter(is_official=True).first()
        self.assertEqual(self.client.get(reverse("sport:test_edit", args=[official.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("sport:test_new") + f"?from={official.pk}").status_code, 200)

    def test_children_needs_quality_part(self):
        self.client.force_login(self.org)
        resp = self.client.post(reverse("sport:test_new"), self.data(kind="children"))
        self.assertContains(resp, "Качество езды")
        resp = self.client.post(reverse("sport:test_new"), self.data(kind="children", second_collective="Посадка | 2\nСредства управления | 2"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual([p["key"] for p in DressageTest.objects.get(name="Клубная езда №1").parts], ["technique", "quality"])

    def test_guest_and_participant(self):
        url = reverse("sport:test_list")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.athlete)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("sport:test_new"), self.data()).status_code, 403)
